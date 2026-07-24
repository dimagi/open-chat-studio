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
from rest_framework.permissions import SAFE_METHODS, BasePermission, DjangoModelPermissions, IsAuthenticated
from rest_framework_api_key.permissions import KeyParser

from apps.api.session_tokens import session_token_expired, validate_session_token
from apps.channels.models import ExperimentChannel, WidgetAuthLevel
from apps.channels.utils import extract_domain_from_headers, get_experiment_session_cached, validate_domain
from apps.oauth.permissions import is_client_credentials_request
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


class WidgetDomainPermission(BasePermission):
    def has_permission(self, request, view):
        if not isinstance(request.auth, ExperimentChannel):
            # not authed with widget token
            return True

        origin_domain = extract_domain_from_headers(request)
        if not origin_domain:
            return False

        experiment_channel = request.auth
        allowed_domains = experiment_channel.extra_data.get("allowed_domains", [])
        return validate_domain(origin_domain, allowed_domains)


class SessionAccessPermission(BasePermission):
    """Object-capability check for chat session endpoints.

    Token-required sessions demand a valid X-Session-Token (or an
    authenticated user with rights to the session). Legacy sessions
    (session_token_required=False) keep the historical behavior.
    """

    def has_permission(self, request, view):
        session = get_experiment_session_cached(view.kwargs.get("session_id"))
        if not session:
            return False

        if not session.session_token_required:
            return self._has_legacy_access(request, session)

        if self._user_is_session_participant(request.user, session):
            return True

        return self._token_grants_access(request, session)

    def _token_grants_access(self, request, session) -> bool:
        token = request.headers.get("X-Session-Token")
        if not token:
            raise exceptions.PermissionDenied(
                detail={"error": "Session token required", "code": "session_token_required"}
            )
        if not validate_session_token(token, session.external_id):
            raise exceptions.PermissionDenied(
                detail={"error": "Invalid session token", "code": "session_token_invalid"}
            )
        if session_token_expired(session):
            raise exceptions.PermissionDenied(detail={"error": "Session has expired", "code": "session_expired"})
        return True

    def _has_legacy_access(self, request, session) -> bool:
        # Embedded widget channels carry a durable auth policy. The level gates every
        # branch below so a valid embed key can never stand in for a stronger level.
        channel = session.experiment_channel
        level = channel.widget_auth_level if channel is not None else None

        if isinstance(request.auth, ExperimentChannel):
            # The embed key must authenticate *this* session's channel. Auth only proves
            # some channel's key was valid, and WidgetDomainPermission checked that
            # channel's own allowed_domains — so a valid key for a different channel (of
            # the same experiment) must not grant cross-channel access to this session.
            if request.auth != channel:
                return False
            # A valid embed key + domain check satisfies EMBED_KEY and NONE channels. It
            # never satisfies a SESSION_TOKEN channel — that always requires the token,
            # even if the session was (mis)configured with session_token_required=False.
            return level != WidgetAuthLevel.SESSION_TOKEN

        # No embed key. At EMBED_KEY and above a valid embed key is mandatory, so the
        # public / allowlist fallback is only reachable for NONE-level widget channels
        # (and non-widget sessions, where level is None).
        if level is not None and level != WidgetAuthLevel.NONE:
            return False

        experiment = session.experiment
        if experiment.is_public:
            return True

        participant_id = session.participant.identifier
        if not participant_id:
            return False

        return experiment.is_participant_allowed(participant_id)

    def _user_is_session_participant(self, user, session) -> bool:
        return user.is_authenticated and session.participant and session.participant.user_id == user.id


class IsAuthenticatedOrMachineToken(IsAuthenticated):
    """IsAuthenticated that also admits client-credentials (machine) OAuth tokens.

    Machine tokens have no user, so IsAuthenticated would reject them. For those, authorization is
    delegated to the OAuth scope classes (which are always paired with this on protected views).
    """

    def has_permission(self, request, view):
        if is_client_credentials_request(request):
            return True
        return super().has_permission(request, view)


class ReadOnlyAPIKeyPermission(BasePermission):
    """
    Allows only safe methods (GET, HEAD, OPTIONS) for read-only API keys.
    """

    def has_permission(self, request, view):
        if not hasattr(request, "auth") or request.auth is None:
            return False

        if is_client_credentials_request(request):
            # Machine token: no user and not an API key; authorization is delegated to the OAuth
            # scope classes.
            return True

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

    def has_permission(self, request, view):
        if is_client_credentials_request(request):
            # Machine token: no user, so no membership-derived model permissions. Authorization is
            # delegated to the OAuth scope classes.
            return True
        return super().has_permission(request, view)


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
