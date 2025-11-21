from oauth2_provider.contrib.rest_framework import OAuth2Authentication
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
