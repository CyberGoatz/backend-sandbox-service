"""Service-account tokens are trusted on their claims, so their signature must be verified."""

import base64
import json
import time

import pytest
from authlib.jose import JsonWebKey, jwt
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from crczp.sandbox_uag import oidc_jwt

ISSUER = 'https://idp.example/realms/campus'


@pytest.fixture
def signing_key():
	return JsonWebKey.generate_key('RSA', 2048, is_private=True, options={'kid': 'k1'})


@pytest.fixture
def authenticator(mocker, signing_key):
	mocker.patch.dict(
		settings.SANDBOX_UAG,
		{
			'SERVICE_ACCOUNT_CLIENTS': ('academy-provisioner',),
			'ALLOWED_OIDC_PROVIDERS': ({'issuer': ISSUER, 'jwks_uri': f'{ISSUER}/certs'},),
		},
	)
	auth = oidc_jwt.JWTAccessTokenAuthentication()
	mocker.patch.object(auth, 'get_jwks', return_value={'keys': [signing_key.as_dict(is_private=False)]})
	return auth


def _claims(**overrides):
	claims = {
		'iss': ISSUER,
		'sub': 'svc',
		'azp': 'academy-provisioner',
		'sandbox_roles': ['ROLE_SANDBOX-SERVICE_ADMIN'],
		'exp': int(time.time()) + 300,
	}
	claims.update(overrides)
	return claims


def _signed(key, claims):
	return jwt.encode({'alg': 'RS256', 'kid': 'k1'}, claims, key)


def _unsigned(claims):
	def encode(part):
		return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b'=')

	return encode({'alg': 'none'}) + b'.' + encode(claims) + b'.'


def test_valid_service_account_token_is_accepted(authenticator, signing_key):
	claims = authenticator.verify_signature(_signed(signing_key, _claims()))
	assert claims['azp'] == 'academy-provisioner'


def test_forged_unsigned_token_is_rejected(authenticator):
	with pytest.raises(AuthenticationFailed):
		authenticator.verify_signature(_unsigned(_claims()))


def test_token_signed_by_another_key_is_rejected(authenticator):
	other_key = JsonWebKey.generate_key('RSA', 2048, is_private=True, options={'kid': 'k1'})
	with pytest.raises(AuthenticationFailed):
		authenticator.verify_signature(_signed(other_key, _claims()))


def test_expired_token_is_rejected(authenticator, signing_key):
	with pytest.raises(AuthenticationFailed):
		authenticator.verify_signature(_signed(signing_key, _claims(exp=int(time.time()) - 3600)))


def test_unknown_issuer_is_rejected(authenticator, signing_key):
	with pytest.raises(AuthenticationFailed):
		authenticator.verify_signature(_signed(signing_key, _claims(iss='https://evil.example')))


def test_authenticate_verifies_service_account_tokens(authenticator, mocker):
	parent = mocker.patch('oidc_auth.authentication.BearerTokenAuthentication.authenticate')
	request = APIRequestFactory().get('/pools', HTTP_AUTHORIZATION=b'Bearer ' + _unsigned(_claims()))
	with pytest.raises(AuthenticationFailed):
		authenticator.authenticate(request)
	parent.assert_not_called()


def test_authenticate_leaves_user_tokens_to_the_existing_flow(authenticator, mocker):
	parent = mocker.patch('oidc_auth.authentication.BearerTokenAuthentication.authenticate', return_value=None)
	verify = mocker.patch.object(authenticator, 'verify_signature')
	token = _unsigned({'iss': ISSUER, 'sub': 'student', 'azp': 'vit-students'})
	authenticator.authenticate(APIRequestFactory().get('/pools', HTTP_AUTHORIZATION=b'Bearer ' + token))
	verify.assert_not_called()
	parent.assert_called_once()
