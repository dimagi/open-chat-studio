import re

from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.teams.models import Team


class TeamScopedOAuth2Validator(OAuth2Validator):
    def extract_team_scopes(self, scopes):
        """Extract team slugs from scopes."""
        team_pattern = re.compile(r"^team:([a-z0-9-_]+)$")
        team_scopes = []

        for scope in scopes:
            match = team_pattern.match(scope)
            if match:
                team_scopes.append(match.group(1))

        return team_scopes

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        """
        Validate team scopes against user membership. The user must be a member of all the selected teams.
        This happens once the user POSTs to the /authorize/ endpoint, which will first come here to validate the
        requested scopes.
        """
        team_slugs = self.extract_team_scopes(scopes)

        # Validate team scopes - user must be member of each team
        if team_slugs:
            user = request.user
            user_team_slugs = set(user.teams.values_list("slug", flat=True))
            requested_team_slugs = set(team_slugs)

            if not requested_team_slugs.issubset(user_team_slugs):
                return False

        return True

    def validate_bearer_token(self, token, scopes, request):
        """
        When users try to access resources, check that provided token is valid. Validity is determined by
        whether the user is still a member of the teams encoded in the token's scopes. If the user loses any one
        of the team memberships, the token as a whole will no longer be valid.
        """
        # Use parent validation
        valid = super().validate_bearer_token(token, scopes, request)

        if not valid:
            return False

        # Extract team scopes from token
        access_token = request.access_token
        team_slugs = self.extract_team_scopes(access_token.scope.split())

        if team_slugs:
            # Verify user is still member of all teams
            user = request.user
            membership_teams = Team.objects.filter(membership__user=user, slug__in=team_slugs).all()

            if set([team.slug for team in membership_teams]) != set(team_slugs):
                return False

        return True
