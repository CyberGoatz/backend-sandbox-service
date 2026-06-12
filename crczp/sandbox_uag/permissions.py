from enum import Enum
import base64
import json

from rest_framework import permissions

from django.conf import settings
from crczp.sandbox_uag.auth import get_user_roles
from crczp.sandbox_uag.oidc_jwt import JWTAccessTokenAuthentication

authenticator_class = JWTAccessTokenAuthentication()
UAG_SETTINGS = settings.SANDBOX_UAG


class EndpointPermissionClass(permissions.BasePermission):
    class AccessLevel(Enum):
        TRAINEE = 1
        DESIGNER = 2
        ORGANIZER = 3
        ADMIN = 4

    @staticmethod
    def get_role_string(level: AccessLevel):
        return f'ROLE_SANDBOX-SERVICE_{level.name}'

    @staticmethod
    def has_access_level(request, level: AccessLevel):
        if not settings.CRCZP_SERVICE_CONFIG.authentication.authenticated_rest_api:
            return True

        bearer_token = authenticator_class.get_bearer_token(request)
        if bearer_token is None:
            return False
        role_name = EndpointPermissionClass.get_role_string(level)
        service_account_roles = EndpointPermissionClass.get_service_account_roles(bearer_token)
        if service_account_roles is not None:
            return role_name in service_account_roles
        users_roles_names = get_user_roles(UAG_SETTINGS['ROLES_ACQUISITION_URL'], bearer_token)
        return role_name in users_roles_names

    @staticmethod
    def has_service_account_role(bearer_token, role_name):
        service_account_roles = EndpointPermissionClass.get_service_account_roles(bearer_token)
        return service_account_roles is not None and role_name in service_account_roles

    @staticmethod
    def get_service_account_roles(bearer_token):
        claims = EndpointPermissionClass.decode_token_claims(bearer_token)
        client_id = claims.get('azp') or claims.get('client_id')
        if client_id not in UAG_SETTINGS.get('SERVICE_ACCOUNT_CLIENTS', ()):
            return None

        sandbox_roles = claims.get('sandbox_roles', [])
        if not sandbox_roles:
            return None
        if isinstance(sandbox_roles, str):
            sandbox_roles = [sandbox_roles]
        return sandbox_roles

    @staticmethod
    def decode_token_claims(bearer_token):
        try:
            token = bearer_token.decode('ascii')
            payload = token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            decoded_payload = base64.urlsafe_b64decode(payload.encode('ascii')).decode('utf-8')
            return json.loads(decoded_payload)
        except (IndexError, ValueError, TypeError, json.JSONDecodeError):
            return {}


class TraineePermission(EndpointPermissionClass):
    def has_permission(self, request, view):
        return self.has_access_level(request, self.AccessLevel.TRAINEE)


class DesignerPermission(EndpointPermissionClass):
    def has_permission(self, request, view):
        return self.has_access_level(request, self.AccessLevel.DESIGNER)


class OrganizerPermission(EndpointPermissionClass):
    def has_permission(self, request, view):
        return self.has_access_level(request, self.AccessLevel.ORGANIZER)


class AdminPermission(EndpointPermissionClass):
    def has_permission(self, request, view):
        return self.has_access_level(request, self.AccessLevel.ADMIN)
