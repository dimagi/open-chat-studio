"""
OAuth2 authentication and permission classes for team-based access control.

This module provides custom authentication and authorization backends that extend Django REST
Framework's OAuth2 functionality with team-aware scoping. It ensures that:
- Scope validation is only applied to OAuth2 tokens (not other auth types)
- Team membership is validated and attached to requests
- Fine-grained permission control via required scopes
"""

from django.contrib.auth.models import AnonymousUser
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasResourceScope, TokenHasScope
from rest_framework import exceptions

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


def is_client_credentials_request(request) -> bool:
    """True when the request is authenticated by a client-credentials (machine) OAuth token.

    Machine tokens have no user, so the user-based authorization gates (IsAuthenticated,
    DjangoModelPermissions, ...) cannot apply. Their authorization instead rests entirely on the
    OAuth scope (enforced by TokenHasOAuthScope / TokenHasOAuthResourceScope) and the team pinned on
    the token.
    """
    token = getattr(request, "auth", None)
    if not isinstance(token, OAuth2AccessToken) or token.application_id is None:
        return False
    return token.application.authorization_grant_type == OAuth2Application.GRANT_CLIENT_CREDENTIALS


def application_allows_chatbot(request, experiment) -> bool:
    """True when the request is authorised for `experiment` at all -- to chat with it or to edit it.

    Only client-credentials (machine) applications are pinned to a set of chatbots: their token is
    handed to a machine (and, for the chat widget, to a browser) with no user behind it, so the team
    boundary is too coarse to be the last line. Every other caller -- API key, Django session,
    authorization-code token -- keeps team-membership semantics untouched.

    The allowlist holds working versions, but a caller may address a version directly: `public_id` is
    unique per row and `create_new_version` assigns a fresh one. Normalise to the family head so a
    legitimate versioned call is not denied.
    """
    if not is_client_credentials_request(request):
        return True
    return request.auth.application.allowed_chatbots.filter(pk=experiment.get_working_version_id()).exists()


def enforce_application_chatbot_access(request, experiment) -> None:
    """Raise `PermissionDenied` unless the request may start a chat with `experiment`.

    403 rather than 401: the caller authenticated fine, it just isn't authorised for this chatbot.
    Call this before anything with a side effect -- a session, a participant -- is created.
    """
    if not application_allows_chatbot(request, experiment):
        raise exceptions.PermissionDenied("This application is not authorized to interact with this chatbot.")


def enforce_application_chatbot_write(request, experiment) -> None:
    """Raise `PermissionDenied` unless the request may modify `experiment`'s configuration.

    Same allowlist as the chat path, different message: reconfiguring a chatbot is at least as
    sensitive as conversing with it, so a machine token reaches only the chatbots it was pinned to.
    Creating a chatbot is deliberately *not* gated -- a chatbot that does not exist yet cannot be on
    any allowlist, so gating it would leave a machine token unable to bootstrap one.
    """
    if not application_allows_chatbot(request, experiment):
        raise exceptions.PermissionDenied("This application is not authorized to modify this chatbot.")


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
