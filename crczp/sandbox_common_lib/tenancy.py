"""
Campus tenancy for sandbox definitions and pools.

The tenant of a request is the first alias in the token's ``organization`` claim, which Keycloak
emits either as a list (``["vit"]``) or as an object keyed by alias (``{"vit": {...}}``).
A caller without the claim belongs to no tenant and is treated exactly as before, with one
exception: a sandbox administrator without the claim (the backend-cybergoatz service account) may
act for a tenant by sending ``X-Tenant-Key: <slug>``. The header is ignored for everybody else.

A tenant caller sees rows of its own tenant and rows without a tenant (the global library).
Global rows are read-only for it, and rows of other tenants answer 404 so their ids do not leak.
"""

import re

from django.conf import settings
from django.db.models import Q, QuerySet
from rest_framework import permissions
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from crczp.sandbox_uag.oidc_jwt import JWTAccessTokenAuthentication

ORGANIZATION_CLAIM = 'organization'
TENANT_HEADER = 'HTTP_X_TENANT_KEY'
ADMIN_ROLE = 'ROLE_SANDBOX-SERVICE_ADMIN'
MAX_TENANT_KEY_LENGTH = 40
_TENANT_KEY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
_REQUEST_CACHE_ATTR = '_crczp_tenant_key'
_UNSET = object()

_authenticator = JWTAccessTokenAuthentication()


def tenant_from_claim(claim) -> str | None:
	"""Return the first alias of an ``organization`` claim value, or None."""
	first = None
	if isinstance(claim, dict):
		first = next((key for key in claim if key is not None), None)
	elif isinstance(claim, (list, tuple)):
		first = next((item for item in claim if item is not None), None)
	elif isinstance(claim, str):
		first = claim
	if first is None:
		return None
	alias = str(first).strip()
	if not alias or len(alias) > MAX_TENANT_KEY_LENGTH:
		return None
	return alias


def _token_claims(request) -> dict:
	header = request.META.get('HTTP_AUTHORIZATION', '')
	parts = header.split()
	if len(parts) != 2 or parts[0].lower() != 'bearer':
		return {}
	return _authenticator.get_token_claims(parts[1].encode('ascii', errors='ignore')) or {}


def _auth_enabled() -> bool:
	return settings.CRCZP_SERVICE_CONFIG.authentication.authenticated_rest_api


def is_admin(request) -> bool:
	"""True for sandbox administrators (including the service account) and when auth is disabled."""
	if not _auth_enabled():
		return True
	user = getattr(request, 'user', None)
	if user is None or not user.is_authenticated:
		return False
	return user.is_superuser or user.groups.filter(name=ADMIN_ROLE).exists()


def request_tenant(request) -> str | None:
	"""Tenant the request acts for, or None for callers outside any tenant."""
	cached = getattr(request, _REQUEST_CACHE_ATTR, _UNSET)
	if cached is not _UNSET:
		return cached
	claims = _token_claims(request)
	tenant = tenant_from_claim(claims.get(ORGANIZATION_CLAIM))
	if tenant is None and ORGANIZATION_CLAIM not in claims:
		header = request.META.get(TENANT_HEADER)
		if header and is_admin(request):
			tenant = _validated_header_tenant(header)
	setattr(request, _REQUEST_CACHE_ATTR, tenant)
	return tenant


def _validated_header_tenant(header: str) -> str:
	tenant = header.strip()
	if len(tenant) > MAX_TENANT_KEY_LENGTH or not _TENANT_KEY_PATTERN.match(tenant):
		raise ValidationError({'X-Tenant-Key': 'Invalid tenant key.'})
	return tenant


def is_visible(caller_tenant: str | None, row_tenant: str | None) -> bool:
	return caller_tenant is None or row_tenant is None or caller_tenant == row_tenant


def visible_q(caller_tenant: str | None, field: str = 'tenant_key') -> Q:
	if caller_tenant is None:
		return Q()
	return Q(**{f'{field}__isnull': True}) | Q(**{field: caller_tenant})


def filter_visible(queryset: QuerySet, request, field: str = 'tenant_key') -> QuerySet:
	"""Restrict a queryset to the rows the caller may see."""
	tenant = request_tenant(request)
	if tenant is None:
		return queryset
	return queryset.filter(visible_q(tenant, field))


def check_readable(request, row_tenant: str | None) -> None:
	if not is_visible(request_tenant(request), row_tenant):
		raise NotFound()


def check_writable(request, row_tenant: str | None) -> None:
	tenant = request_tenant(request)
	if tenant is None:
		return
	if row_tenant is None:
		raise PermissionDenied('Rows of the global library are read-only for tenant users.')
	if row_tenant != tenant:
		raise NotFound()


def filter_pool_visibility(queryset: QuerySet, request) -> QuerySet:
	"""Hide pools with ``visible=False`` from everybody but their creator and administrators."""
	if is_admin(request):
		return queryset
	user = getattr(request, 'user', None)
	if user is not None and user.is_authenticated:
		return queryset.filter(Q(visible=True) | Q(created_by=user))
	return queryset.filter(visible=True)


def _resolve_tenant_key(view, request):
	"""
	Find the tenant key of the definition or pool a URL refers to.

	:return: (found, tenant_key); found is False when the URL does not name such a resource
	or when the resource does not exist (the view answers 404 itself in that case).
	"""
	# Imported here to keep this module importable from settings-free code paths.
	from crczp.sandbox_definition_app.models import Definition
	from crczp.sandbox_instance_app.models import (
		AllocationRequest,
		CleanupRequest,
		Pool,
		Sandbox,
		SandboxAllocationUnit,
	)

	kwargs = getattr(view, 'kwargs', {}) or {}
	lookups = []
	if 'definition_id' in kwargs:
		lookups.append(Definition.objects.filter(pk=kwargs['definition_id']).values_list('tenant_key', flat=True))
	if 'pool_id' in kwargs:
		lookups.append(Pool.objects.filter(pk=kwargs['pool_id']).values_list('tenant_key', flat=True))
	if 'unit_id' in kwargs:
		lookups.append(SandboxAllocationUnit.objects.filter(pk=kwargs['unit_id']).values_list('pool__tenant_key', flat=True))
	if 'request_id' in kwargs:
		model = CleanupRequest if '/cleanup-requests/' in request.path else AllocationRequest
		lookups.append(
			model.objects.filter(pk=kwargs['request_id']).values_list('allocation_unit__pool__tenant_key', flat=True)
		)
	if 'sandbox_uuid' in kwargs:
		lookups.append(
			Sandbox.objects.filter(pk=kwargs['sandbox_uuid']).values_list('allocation_unit__pool__tenant_key', flat=True)
		)
	results = []
	for lookup in lookups:
		rows = list(lookup[:1])
		if rows:
			results.append(rows[0])
	return results


# Trainee flow: the training service locks a sandbox of the instance's pool for the trainee.
_WRITE_ALLOWED_ON_GLOBAL = {'pool-sandbox-get-and-lock'}


class TenantAccessPermission(permissions.BasePermission):
	"""
	Applies the tenant rule to every endpoint that names a definition, pool, allocation unit,
	request or sandbox in its URL: other tenants' rows answer 404 and tenant callers may not
	change rows of the global library.
	"""

	def has_permission(self, request, view):
		if request_tenant(request) is None:
			return True
		url_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
		for row_tenant in _resolve_tenant_key(view, request):
			if request.method in permissions.SAFE_METHODS or url_name in _WRITE_ALLOWED_ON_GLOBAL:
				check_readable(request, row_tenant)
			else:
				check_writable(request, row_tenant)
		return True
