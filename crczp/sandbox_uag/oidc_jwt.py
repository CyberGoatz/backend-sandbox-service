import requests
import structlog
import base64
import json
import time

from urllib.parse import urlparse
from django.conf import settings
from django.core.cache import caches
from django.utils.translation import gettext as _
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from jwkest.jwt import JWT
from rest_framework.exceptions import AuthenticationFailed

from oidc_auth.authentication import BearerTokenAuthentication
from oidc_auth.settings import api_settings as oidc_auth_settings


LOG = structlog.get_logger()
WELL_KNOWN_CONFIG_CACHE_TTL = oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME
CACHE = caches['default']
OIDC_SUB_PREFIX = 'oidc-sub-'
JWKS_PREFIX = 'oidc-jwks-'
JWKS_CACHE_TTL = oidc_auth_settings.OIDC_JWKS_EXPIRATION_TIME
# Asymmetric algorithms only: never 'none' or HMAC with a public key.
SERVICE_ACCOUNT_JWT = JsonWebToken(['RS256', 'RS384', 'RS512', 'PS256', 'PS384', 'PS512', 'ES256', 'ES384', 'ES512'])
CLOCK_SKEW_SECONDS = 60
WELL_KNOWN_CONFIG_PREFIX = 'oidc-well-known-'


def _cache_get(key):
    try:
        return CACHE.get(key)
    except Exception as ex:
        LOG.warning('OIDC cache get failed; continuing without cache.', key=key, error=str(ex))
        return None


def _cache_set(key, value, timeout):
    try:
        CACHE.set(key, value, timeout)
    except Exception as ex:
        LOG.warning('OIDC cache set failed; continuing without cache.', key=key, error=str(ex))


class JWTAccessTokenAuthentication(BearerTokenAuthentication):
    """Use for Bearer token in JWT format. It allows multiple OIDC providers support."""

    def authenticate(self, request):
        bearer_token = self.get_bearer_token(request)
        if bearer_token is not None and self.is_service_account_token(self.get_token_claims(bearer_token)):
            # Service-account tokens are trusted on their claims (no userinfo or user-and-group call),
            # so their signature must be checked on every request.
            self.verify_signature(bearer_token)
        return super().authenticate(request)

    @staticmethod
    def is_service_account_token(claims: dict) -> bool:
        client_id = claims.get('azp') or claims.get('client_id')
        return client_id in settings.SANDBOX_UAG.get('SERVICE_ACCOUNT_CLIENTS', ()) and bool(claims.get('sandbox_roles'))

    def verify_signature(self, token: bytes) -> dict:
        """Verify signature, issuer and expiry of a JWT against the issuer's JWKS."""
        failed = AuthenticationFailed(_('Service-account token could not be verified.'))
        try:
            issuer = self.extract_issuer(token)
        except Exception as ex:
            raise failed from ex
        providers = {provider['issuer']: provider for provider in settings.SANDBOX_UAG['ALLOWED_OIDC_PROVIDERS']}
        if issuer not in providers:
            raise AuthenticationFailed(_('Issuer of the token is not listed in ALLOWED_OIDC_PROVIDERS.'))
        provider = providers[issuer]
        errors = (JoseError, ValueError, KeyError, requests.RequestException)
        try:
            claims = self._decode(token, self.get_jwks(provider))
        except errors:
            try:
                # The issuer may have rotated its keys since they were cached.
                claims = self._decode(token, self.get_jwks(provider, refresh=True))
            except errors as ex:
                LOG.warning('Service-account token verification failed.', error=str(ex))
                raise failed from ex
        if str(claims.get('iss', '')).rstrip('/') != issuer:
            raise failed
        return claims

    @staticmethod
    def _decode(token: bytes, jwks: dict):
        claims = SERVICE_ACCOUNT_JWT.decode(token.decode('ascii'), JsonWebKey.import_key_set(jwks))
        claims.validate(now=int(time.time()), leeway=CLOCK_SKEW_SECONDS)
        if 'exp' not in claims:
            raise ValueError('Token has no expiry.')
        return claims

    def get_jwks(self, provider: dict, refresh: bool = False) -> dict:
        jwks_uri = provider.get('jwks_uri') or self.get_well_known_config(provider)['jwks_uri']
        cache_key = JWKS_PREFIX + jwks_uri
        if not refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        response = requests.get(jwks_uri)
        response.raise_for_status()
        jwks = response.json()
        _cache_set(cache_key, jwks, JWKS_CACHE_TTL)
        return jwks

    @staticmethod
    def extract_issuer(token):
        data = JWT().unpack(token).payload()
        return data.get('iss').rstrip('/')

    def get_well_known_config(self, provider):
        well_known_config_url = provider.get('well_known_config')
        if not well_known_config_url:
            well_known_config_url = provider['issuer'] + '/.well-known/openid-configuration'

        cache_key = WELL_KNOWN_CONFIG_PREFIX + well_known_config_url
        cached_config = _cache_get(cache_key)
        if cached_config is not None:
            return cached_config

        response = requests.get(well_known_config_url)
        response.raise_for_status()
        well_known_config = response.json()
        _cache_set(cache_key, well_known_config, WELL_KNOWN_CONFIG_CACHE_TTL)
        return well_known_config

    def _get_userinfo(self, token):
        issuer = self.extract_issuer(token)
        LOG.debug("Issuer extracted from token.", issuer=issuer)
        allowed_issuers = {provider['issuer']: provider
                           for provider in settings.SANDBOX_UAG['ALLOWED_OIDC_PROVIDERS']}
        if issuer not in allowed_issuers:
            msg = _('Issuer of the token is not listed in ALLOWED_OIDC_PROVIDERS.')
            raise AuthenticationFailed(msg)

        url = urlparse(issuer)
        if url.scheme != 'https':
            LOG.warn('DANGER! OIDC issuer is not using https protocol.', issuer=issuer)

        provider = allowed_issuers[issuer]
        token_claims = self.get_token_claims(token)
        client_id = token_claims.get('azp') or token_claims.get('client_id')
        if client_id in settings.SANDBOX_UAG.get('SERVICE_ACCOUNT_CLIENTS', ()) \
                and token_claims.get('sandbox_roles'):
            preferred_username = token_claims.get('preferred_username') or f'service-account-{client_id}'
            return {
                'sub': token_claims.get('sub'),
                'preferred_username': preferred_username,
                'name': preferred_username,
                'given_name': preferred_username,
                'family_name': 'service-account',
                'email': token_claims.get('email') or f'{preferred_username}@service.local',
            }

        well_known_config = self.get_well_known_config(provider)
        userinfo_endpoint = provider.get('userinfo_endpoint')
        if not userinfo_endpoint:
            userinfo_endpoint = well_known_config['userinfo_endpoint']
        http_headers = {'Authorization': 'Bearer {0}'.format(token.decode('ascii'))}

        response = requests.get(userinfo_endpoint, headers=http_headers)
        response.raise_for_status()

        return response.json()

    @staticmethod
    def get_token_claims(token):
        try:
            token_decoded = token.decode('ascii')
            payload = token_decoded.split(".")[1]
            payload += '=' * (-len(payload) % 4)
            payload_bytes = payload.encode("ascii")
            payload_string = base64.urlsafe_b64decode(payload_bytes).decode("utf-8")
            return json.loads(payload_string)
        except Exception as e:
            LOG.warn(f'An exception occurred during parsing of the token claims: {e}')
            return {}

    def get_userinfo(self, token):
        sub = ""
        userinfo = None
        try:
            sub = self.get_token_claims(token).get('sub', '')
            if sub:
                userinfo = _cache_get(OIDC_SUB_PREFIX + sub)
        except Exception as e:
            LOG.warn(f'An exception occurred during parsing of the token: {e}')

        if not userinfo:
            userinfo = self._get_userinfo(token)
            if sub:
                _cache_set(OIDC_SUB_PREFIX + sub, userinfo,
                           oidc_auth_settings.OIDC_BEARER_TOKEN_EXPIRATION_TIME)
            else:
                LOG.warn(
                    'Sub was not found in the token, the result of this authentication will not be cached')
        return userinfo
