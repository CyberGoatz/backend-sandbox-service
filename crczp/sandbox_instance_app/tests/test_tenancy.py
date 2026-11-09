"""Campus tenancy: tenant_key on definitions and pools, Pool.visible and X-Tenant-Key."""

import base64
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group, User
from django.http import Http404
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from crczp.sandbox_common_lib import tenancy
from crczp.sandbox_definition_app.lib import definitions
from crczp.sandbox_definition_app.models import Definition
from crczp.sandbox_instance_app.lib import pools
from crczp.sandbox_instance_app.models import Pool
from crczp.sandbox_instance_app.views import PoolDetailDeleteUpdateView, PoolListCreateView

pytestmark = pytest.mark.django_db


def _token(claims: dict) -> str:
	def encode(part: dict) -> str:
		return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip('=')

	return f'{encode({"alg": "none"})}.{encode(claims)}.signature'


def _request(user=None, organization=None, tenant_header=None, method='get', url_name=None):
	claims = {'sub': 'user', 'iss': 'https://idp.example'}
	if organization is not None:
		claims[tenancy.ORGANIZATION_CLAIM] = organization
	headers = {'HTTP_AUTHORIZATION': f'Bearer {_token(claims)}'}
	if tenant_header is not None:
		headers['HTTP_X_TENANT_KEY'] = tenant_header
	django_request = getattr(APIRequestFactory(), method)('/pools', **headers)
	django_request.resolver_match = SimpleNamespace(url_name=url_name)
	request = Request(django_request)
	request.user = user
	return request


def _view(view_class, request, **kwargs):
	view = view_class()
	view.request = request
	view.kwargs = kwargs
	view.format_kwarg = None
	return view


@pytest.fixture
def auth_enabled(mocker):
	mocker.patch.object(tenancy, '_auth_enabled', return_value=True)


@pytest.fixture
def organizer():
	return User.objects.create(username='organizer')


@pytest.fixture
def other_organizer():
	return User.objects.create(username='other-organizer')


@pytest.fixture
def admin():
	user = User.objects.create(username='service-account')
	group, _ = Group.objects.get_or_create(name=tenancy.ADMIN_ROLE)
	user.groups.add(group)
	return user


@pytest.fixture
def tenant_pools(definition, organizer):
	def make(tenant_key, visible=True, created_by=organizer):
		return Pool.objects.create(
			definition=definition,
			max_size=1,
			private_management_key='k',
			public_management_key='k',
			uuid=f'{tenant_key}-{visible}',
			created_by=created_by,
			visible=visible,
			tenant_key=tenant_key,
		)

	return SimpleNamespace(global_pool=make(None), vit=make('vit'), other=make('other'))


class TestClaimParsing:
	def test_array_form(self):
		assert tenancy.tenant_from_claim(['vit', 'other']) == 'vit'

	def test_object_form(self):
		assert tenancy.tenant_from_claim({'vit': {'id': '6f1c'}, 'other': {}}) == 'vit'

	def test_missing_or_unusable(self):
		for claim in (None, [], {}, [' '], 42, ['x' * (tenancy.MAX_TENANT_KEY_LENGTH + 1)]):
			assert tenancy.tenant_from_claim(claim) is None


class TestRequestTenant:
	def test_tenant_comes_from_the_token(self, auth_enabled, organizer):
		assert tenancy.request_tenant(_request(organizer, organization=['vit'])) == 'vit'
		assert tenancy.request_tenant(_request(organizer, organization={'vit': {}})) == 'vit'

	def test_no_claim_means_no_tenant(self, auth_enabled, organizer):
		assert tenancy.request_tenant(_request(organizer)) is None

	def test_header_is_ignored_for_non_admins(self, auth_enabled, organizer):
		assert tenancy.request_tenant(_request(organizer, tenant_header='vit')) is None

	def test_header_is_honored_for_the_admin_service_account(self, auth_enabled, admin):
		assert tenancy.request_tenant(_request(admin, tenant_header='vit')) == 'vit'

	def test_header_cannot_override_a_claim(self, auth_enabled, admin):
		assert tenancy.request_tenant(_request(admin, organization=['vit'], tenant_header='other')) == 'vit'

	def test_invalid_header_is_rejected(self, auth_enabled, admin):
		with pytest.raises(ValidationError):
			tenancy.request_tenant(_request(admin, tenant_header='bad key!'))


class TestPoolVisibility:
	def test_tenant_sees_own_and_global_pools(self, auth_enabled, organizer, tenant_pools):
		view = _view(PoolListCreateView, _request(organizer, organization=['vit']))
		ids = set(view.get_queryset().values_list('id', flat=True))
		assert tenant_pools.global_pool.id in ids
		assert tenant_pools.vit.id in ids
		assert tenant_pools.other.id not in ids

	def test_callers_without_claim_see_all_tenants(self, auth_enabled, organizer, tenant_pools):
		view = _view(PoolListCreateView, _request(organizer))
		ids = set(view.get_queryset().values_list('id', flat=True))
		assert {tenant_pools.global_pool.id, tenant_pools.vit.id, tenant_pools.other.id} <= ids

	def test_hidden_pool_is_listed_only_to_creator_and_admin(
		self, auth_enabled, organizer, other_organizer, admin, definition
	):
		hidden = Pool.objects.create(
			definition=definition,
			max_size=1,
			private_management_key='k',
			public_management_key='k',
			uuid='hidden',
			created_by=organizer,
			visible=False,
		)

		def listed(user):
			return hidden.id in set(_view(PoolListCreateView, _request(user)).get_queryset().values_list('id', flat=True))

		assert listed(organizer)
		assert listed(admin)
		assert not listed(other_organizer)

	def test_hidden_pool_detail_is_not_found_for_other_organizers(self, auth_enabled, organizer, other_organizer, definition):
		hidden = Pool.objects.create(
			definition=definition,
			max_size=1,
			private_management_key='k',
			public_management_key='k',
			uuid='hidden',
			created_by=organizer,
			visible=False,
		)
		view = _view(PoolDetailDeleteUpdateView, _request(other_organizer), pool_id=hidden.id)
		with pytest.raises(Http404):
			view.get_object()
		view = _view(PoolDetailDeleteUpdateView, _request(organizer), pool_id=hidden.id)
		assert view.get_object() == hidden


class TestTenantAccessPermission:
	def check(self, request, **kwargs):
		return tenancy.TenantAccessPermission().has_permission(request, SimpleNamespace(kwargs=kwargs))

	def test_other_tenants_pool_is_not_found(self, auth_enabled, organizer, tenant_pools):
		with pytest.raises(NotFound):
			self.check(_request(organizer, organization=['vit']), pool_id=tenant_pools.other.id)

	def test_own_and_global_pools_are_readable(self, auth_enabled, organizer, tenant_pools):
		request = _request(organizer, organization=['vit'])
		assert self.check(request, pool_id=tenant_pools.vit.id)
		assert self.check(request, pool_id=tenant_pools.global_pool.id)

	def test_global_pool_is_read_only_for_tenants(self, auth_enabled, organizer, tenant_pools):
		with pytest.raises(PermissionDenied):
			self.check(_request(organizer, organization=['vit'], method='delete'), pool_id=tenant_pools.global_pool.id)
		assert self.check(_request(organizer, organization=['vit'], method='delete'), pool_id=tenant_pools.vit.id)

	def test_trainee_sandbox_lock_works_on_global_pools(self, auth_enabled, organizer, tenant_pools):
		request = _request(organizer, organization=['vit'], method='post', url_name='pool-sandbox-get-and-lock')
		assert self.check(request, pool_id=tenant_pools.global_pool.id)
		with pytest.raises(NotFound):
			self.check(request, pool_id=tenant_pools.other.id)

	def test_nested_resources_resolve_to_their_pool(self, auth_enabled, organizer, sandbox):
		Pool.objects.filter(pk=sandbox.allocation_unit.pool_id).update(tenant_key='other')
		request = _request(organizer, organization=['vit'])
		with pytest.raises(NotFound):
			self.check(request, unit_id=sandbox.allocation_unit.id)
		with pytest.raises(NotFound):
			self.check(request, sandbox_uuid=sandbox.id)

	def test_other_tenants_definition_is_not_found(self, auth_enabled, organizer, definition):
		Definition.objects.filter(pk=definition.pk).update(tenant_key='other')
		with pytest.raises(NotFound):
			self.check(_request(organizer, organization=['vit']), definition_id=definition.id)

	def test_callers_without_claim_are_unaffected(self, auth_enabled, organizer, tenant_pools):
		assert self.check(_request(organizer, method='delete'), pool_id=tenant_pools.other.id)

	def test_admin_acting_for_a_tenant_is_scoped_to_it(self, auth_enabled, admin, tenant_pools):
		with pytest.raises(NotFound):
			self.check(_request(admin, tenant_header='vit'), pool_id=tenant_pools.other.id)


class TestCreateWithTenant:
	@pytest.fixture(autouse=True)
	def set_up(self, mocker, image):
		mocker.patch('crczp.sandbox_common_lib.utils.get_terraform_client')
		mocker.patch('crczp.sandbox_cloud_app.lib.projects.list_images', return_value=[image])
		mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_definition')
		mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_containers')
		mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_topology_definition')
		mocker.patch('crczp.sandbox_definition_app.lib.definitions.validate_docker_containers')
		mock_repo = mocker.patch('crczp.sandbox_definition_app.lib.definitions.get_def_provider')
		mock_repo.return_value.get_rev_sha = mocker.MagicMock(return_value='sha')

	def test_pool_is_stamped_with_the_tenant(self, definition, created_by, get_terraform_client):
		pool = pools.create_pool(dict(definition_id=definition.id, max_size=2), created_by, tenant_key='vit')
		assert pool.tenant_key == 'vit'

	def test_pool_without_tenant_stays_global(self, definition, created_by, get_terraform_client):
		pool = pools.create_pool(dict(definition_id=definition.id, max_size=2), created_by)
		assert pool.tenant_key is None

	def test_pool_cannot_use_another_tenants_definition(self, definition, created_by, get_terraform_client):
		Definition.objects.filter(pk=definition.pk).update(tenant_key='other')
		with pytest.raises(Http404):
			pools.create_pool(dict(definition_id=definition.id, max_size=2), created_by, tenant_key='vit')

	def test_definition_is_stamped_with_the_tenant(self, created_by, mocker):
		mocker.patch(
			'crczp.sandbox_definition_app.lib.definitions.get_definition', return_value=SimpleNamespace(name='tenant-def')
		)
		definition = definitions.create_definition('https://git.example/tenant.git', created_by, 'main', tenant_key='vit')
		assert definition.tenant_key == 'vit'
