import base64
import json

from crczp.sandbox_uag import permissions


def _token(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).rstrip(b"=")
    return b"header." + payload + b".signature"


def test_service_account_permission_miss_does_not_call_uag(mocker):
    token = _token({
        "azp": "training-service",
        "sandbox_roles": "ROLE_SANDBOX-SERVICE_ORGANIZER",
    })
    request = object()

    mocker.patch.object(permissions.authenticator_class, "get_bearer_token", return_value=token)
    mocker.patch.object(
        permissions,
        "UAG_SETTINGS",
        {
            "SERVICE_ACCOUNT_CLIENTS": ("training-service",),
            "ROLES_ACQUISITION_URL": "http://uag/users/info",
        },
    )
    get_user_roles = mocker.patch.object(permissions, "get_user_roles")

    assert not permissions.EndpointPermissionClass.has_access_level(
        request,
        permissions.EndpointPermissionClass.AccessLevel.TRAINEE,
    )
    get_user_roles.assert_not_called()

    assert permissions.EndpointPermissionClass.has_access_level(
        request,
        permissions.EndpointPermissionClass.AccessLevel.ORGANIZER,
    )
    get_user_roles.assert_not_called()


def test_non_service_account_permission_uses_uag(mocker):
    token = _token({"azp": "frontend-client"})
    request = object()

    mocker.patch.object(permissions.authenticator_class, "get_bearer_token", return_value=token)
    mocker.patch.object(
        permissions,
        "UAG_SETTINGS",
        {
            "SERVICE_ACCOUNT_CLIENTS": ("training-service",),
            "ROLES_ACQUISITION_URL": "http://uag/users/info",
        },
    )
    get_user_roles = mocker.patch.object(
        permissions,
        "get_user_roles",
        return_value=["ROLE_SANDBOX-SERVICE_TRAINEE"],
    )

    assert permissions.EndpointPermissionClass.has_access_level(
        request,
        permissions.EndpointPermissionClass.AccessLevel.TRAINEE,
    )
    get_user_roles.assert_called_once_with("http://uag/users/info", token)
