"""
OAuth2 authentication and permission classes for team-based access control.

This module provides custom authentication and authorization backends that extend Django REST
Framework's OAuth2 functionality with team-aware scoping. It ensures that:
- Scope validation is only applied to OAuth2 tokens (not other auth types)
- Team membership is validated and attached to requests
- Fine-grained permission control via required scopes
"""

from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasResourceScope, TokenHasScope
from rest_framework import exceptions

from apps.teams.helpers import get_team_membership_for_request

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

        request.user = user
        request.team = access_token.team
        request.team_membership = get_team_membership_for_request(request)
        if not request.team_membership:
            raise exceptions.AuthenticationFailed()

        return user, access_token


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
