"""
OAuth2 authentication and permission classes for team-based access control.

This module provides custom authentication and authorization backends that extend Django REST
Framework's OAuth2 functionality with team-aware scoping. It ensures that:
- Scope validation is only applied to OAuth2 tokens (not other auth types)
- Team membership is validated and attached to requests
- Fine-grained permission control via required scopes
"""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasResourceScope, TokenHasScope
from rest_framework import exceptions

from apps.api.exceptions import ChatApiAccessDenied
from apps.oauth.models import OAuth2Application
from apps.teams.helpers import SyntheticTeamMembership, get_team_membership_for_request
from apps.teams.utils import set_current_team

from .models import OAuth2AccessToken


class OAuth2AccessTokenAuthentication(OAuth2Authentication):
    """
    OAuth2 authentication backend that sets the team on the request.
    """

    def authenticate(self, request):
        """
        Returns two-tuple of (user, token) if authentication succeeds,
        or None otherwise.
        """
        response = super().authenticate(request)
        if response is None:
            return

        user, access_token = response
        request.team = access_token.team

        application = access_token.application
        is_client_credentials = (
            application is not None
            and application.authorization_grant_type == OAuth2Application.GRANT_CLIENT_CREDENTIALS
        )
        if is_client_credentials:
            # Machine token: no user and no Membership row. Access is granted by the token's pinned
            # team alone (a synthetic service identity), so skip the human membership gate.
            user = AnonymousUser()
            request.user = user
            request.team_membership = SyntheticTeamMembership(access_token.team)
        else:
            request.user = user
            request.team_membership = get_team_membership_for_request(request)
            if not request.team_membership:
                raise exceptions.AuthenticationFailed()

        # this is unset by the request_finished signal
        set_current_team(access_token.team)
        return user, access_token


def is_client_credentials_token(token) -> bool:
    """True when `token` is a client-credentials (machine) OAuth token.

    Takes the token rather than the request because an authentication class resolves the token
    *inside* `authenticate()`, before `request.auth` exists.
    """
    if not isinstance(token, OAuth2AccessToken) or token.application_id is None:
        return False
    return token.application.authorization_grant_type == OAuth2Application.GRANT_CLIENT_CREDENTIALS


def is_client_credentials_request(request) -> bool:
    """True when the request is authenticated by a client-credentials (machine) OAuth token.

    Machine tokens have no user, so the user-based authorization gates (IsAuthenticated,
    DjangoModelPermissions, ...) cannot apply. Their authorization instead rests entirely on the
    OAuth scope (enforced by TokenHasOAuthScope / TokenHasOAuthResourceScope) and the team pinned on
    the token.
    """
    return is_client_credentials_token(getattr(request, "auth", None))


def token_allows_chatbot(token, experiment) -> bool:
    """True when `token` may start a chat with `experiment`.

    Only client-credentials (machine) applications are pinned to a set of chatbots: their token is
    handed to a machine (and, for the chat widget, to a browser) with no user behind it, so the team
    boundary is too coarse to be the last line. Every other caller -- API key, Django session,
    authorization-code token -- keeps team-membership semantics untouched.

    The allowlist holds working versions, but a caller may address a version directly: `public_id` is
    unique per row and `create_new_version` assigns a fresh one. Normalise to the family head so a
    legitimate versioned call is not denied.
    """
    if not is_client_credentials_token(token):
        return True
    return token.application.allowed_chatbots.filter(pk=experiment.get_working_version_id()).exists()


def application_allows_chatbot(request, experiment) -> bool:
    """True when the request may start a chat with `experiment`."""
    return token_allows_chatbot(getattr(request, "auth", None), experiment)


def enforce_application_chatbot_access(request, experiment) -> None:
    """Raise `PermissionDenied` unless the request may start a chat with `experiment`.

    403 rather than 401: the caller authenticated fine, it just isn't authorised for this chatbot.
    Call this before anything with a side effect -- a session, a participant -- is created.

    Not for `chat/start/`, where the allowlist is one of several admission checks that collapse
    into a single uniform 401: call `token_allows_chatbot` there and let the authenticator raise,
    so the response does not leak which check failed.
    """
    if not application_allows_chatbot(request, experiment):
        raise exceptions.PermissionDenied("This application is not authorized to interact with this chatbot.")


def token_admits_chatbot(token, experiment) -> bool:
    """Whether `token` meets every condition the chat door asks of a machine credential.

    Four conditions, and the reason each is here rather than left to the generic OAuth gates:

    - **Client credentials.** An authorization-code token takes its team from a `Grant` plus a live
      membership check, and admitting one raises a question this door does not need to answer (may a
      signed-in user's token chat as an anonymous participant?).
    - **Team.** A token pinned to team A must not reach team B's chatbot.
    - **`chat:start` and nothing broader.** A `chatbots:interact` token also converses with every
      chatbot in the team and sends outbound WhatsApp/Telegram messages to arbitrary participants,
      which is the wrong credential to put in page JavaScript.
    - **The application's allowlist.** `chat:start` is *team*-scoped, and the token is placed in a
      browser by design, so the team boundary is too coarse to be the last line.

    One predicate rather than four guard clauses because the caller collapses them into a single
    uniform refusal anyway: which condition failed goes to the logs, never to the response.
    """
    return (
        is_client_credentials_token(token)
        and token.team_id == experiment.team_id
        and token.is_valid([settings.CHAT_API_SCOPE])
        and token_allows_chatbot(token, experiment)
    )


def validated_machine_token(request, experiment) -> OAuth2AccessToken:
    """The request's client-credentials token, or raise `ChatApiAccessDenied` if it is not valid
    for starting a chat session with `experiment`.

    The caller must already have established that an `Authorization` header is present: every path
    out of here is either a token or a refusal, never a silent None. `OAuth2Authentication` returns
    None for an *invalid or expired* token exactly as it does for no token at all, so treating that
    None as "no credential was offered" would let a revoked token fall through to the embed-key
    authenticator and from there to the still-open keyless path.
    """
    result = OAuth2Authentication().authenticate(request)
    if result is None:
        # Signature, expiry or revocation -- never "no token", which the caller ruled out.
        raise ChatApiAccessDenied()
    _user, token = result
    if not token_admits_chatbot(token, experiment):
        raise ChatApiAccessDenied()
    return token


class TokenHasOAuthScope(TokenHasScope):
    """
    OAuth scope checking should only be done for OAuth2 tokens. This class overrides the
    default behavior to skip scope checking for other token types.
    """

    def has_permission(self, request, view):
        token = request.auth

        if not token:
            return False

        if not isinstance(token, OAuth2AccessToken):
            # Only check OAuth scopes when using OAuth2 tokens
            return True

        return super().has_permission(request, view)


class TokenHasOAuthResourceScope(TokenHasResourceScope, TokenHasOAuthScope):
    """An implementation of TokenHasResourceScope that uses TokenHasOAuthScope"""

    pass
