from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasScope
from rest_framework import exceptions

from apps.teams.helpers import get_team_membership_for_request


class OAuth2TeamsAuthentication(OAuth2Authentication):
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


def TokenHasRequiredScope(*required_scopes):
    """
    Factory function that creates a TokenHasScope permission class with required scopes.
    Works with DRF's @permission_classes decorator.

    Usage:
        @api_view(['GET'])
        @permission_classes([TokenHasRequiredScope('read', 'write')])
        def my_view(request):
            return Response({'message': 'Hello!'})
    """

    class _TokenHasRequiredScope(TokenHasScope):
        def has_permission(self, request, view):
            # Set required_scopes on the view for the parent class to check
            view.required_scopes = required_scopes
            return super().has_permission(request, view)

    return _TokenHasRequiredScope
