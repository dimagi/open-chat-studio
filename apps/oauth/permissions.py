from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from rest_framework import exceptions

from apps.oauth.utils import extract_team_scopes
from apps.teams.helpers import get_team_membership_for_request
from apps.teams.models import Team


class OAuth2TeamsAuthentication(OAuth2Authentication):
    """
    OAuth2 authentication backend that also sets the team on the request.
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
        team_slugs = extract_team_scopes(access_token.scope.split())
        # TODO: We only support a single team per token, but we need to handle multiple teams
        request.team = Team.objects.get(slug=team_slugs[0])
        request.team_membership = get_team_membership_for_request(request)
        if not request.team_membership:
            raise exceptions.AuthenticationFailed()

        return user, access_token
