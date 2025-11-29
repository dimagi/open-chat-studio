import base64
import hashlib
import hmac
import logging
from functools import wraps

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import gettext as _
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import SAFE_METHODS, BasePermission, DjangoModelPermissions
from rest_framework_api_key.permissions import KeyParser

from apps.teams.helpers import get_team_membership_for_request
from apps.teams.utils import set_current_team

from .models import UserAPIKey

logger = logging.getLogger("ocs.api")


class BaseKeyAuthentication(BaseAuthentication):
    def get_key(self, request):
        raise NotImplementedError

    def authenticate(self, request):
        key = self.get_key(request)
        if not key:
            return None

        try:
            token = UserAPIKey.objects.get_from_key(key)
        except UserAPIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed(_("Invalid token.")) from None

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_("User inactive or deleted."))
        user = token.user
        request.user = user
        request.team = token.team
        request.team_membership = get_team_membership_for_request(request)
        if not request.team_membership:
            raise exceptions.AuthenticationFailed()

        # this is unset by the request_finished signal
        set_current_team(token.team)
        return user, token


class ApiKeyAuthentication(BaseKeyAuthentication):
    def get_key(self, request):
        return KeyParser().get_from_header(request, settings.API_KEY_CUSTOM_HEADER)


class BearerTokenAuthentication(BaseKeyAuthentication):
    keyword = "Bearer"

    def get_key(self, request):
        return ConfigurableKeyParser(keyword=self.keyword).get_from_authorization(request)


class ReadOnlyAPIKeyPermission(BasePermission):
    """
    Allows only safe methods (GET, HEAD, OPTIONS) for read-only API keys.
    """

    def has_permission(self, request, view):
        if not hasattr(request, "auth") or request.auth is None:
            return False

        if not request.user or not request.user.is_authenticated:
            return False

        api_key = request.auth
        # The bearer token can also be an oauth Access Token, which doesn't have read_only attribute
        if isinstance(api_key, UserAPIKey) and getattr(api_key, "read_only", True):
            return request.method in SAFE_METHODS

        return True


class ConfigurableKeyParser(KeyParser):
    def __init__(self, keyword: str):
        self.keyword = keyword


class DjangoModelPermissionsWithView(DjangoModelPermissions):
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }


def verify_hmac(view_func):
    """Match the HMAC signature in the request to the calculated HMAC using the request payload."""

    # Based on https://github.com/dimagi/commcare-hq/blob/master/corehq/util/hmac_request.py
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        expected_digest = convert_to_bytestring_if_unicode(request.headers.get("X-Mac-Digest"))
        secret_key_bytes = convert_to_bytestring_if_unicode(settings.COMMCARE_CONNECT_SERVER_SECRET)

        if not (expected_digest and secret_key_bytes):
            logger.exception(
                "Request rejected reason=%s request=%s",
                "hmac:missing_key" if not secret_key_bytes else "hmac:missing_header",
                request.path,
            )
            return HttpResponse(_("Missing HMAC signature or shared key"), status=401)

        data_digest = get_hmac_digest(key=secret_key_bytes, data_bytes=request.body)

        if not hmac.compare_digest(data_digest, expected_digest):
            logger.exception("Calculated HMAC does not match expected HMAC")
            return HttpResponse(_("Invalid payload"), status=401)
        return view_func(request, *args, **kwargs)

    return _inner


def get_hmac_digest(key: bytes, data_bytes: bytes) -> bytes:
    digest = hmac.new(key, data_bytes, hashlib.sha256).digest()
    digest_base64 = base64.b64encode(digest)
    return digest_base64


def convert_to_bytestring_if_unicode(shared_key):
    return shared_key.encode("utf-8") if isinstance(shared_key, str) else shared_key
