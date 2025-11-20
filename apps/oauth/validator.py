from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.oauth.utils import extract_team_scopes


class TeamScopedOAuth2Validator(OAuth2Validator):
    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        """
        Validate team scopes against user membership. The user must be a member of all the selected teams.
        This happens once the user POSTs to the /authorize/ endpoint, which will first come here to validate the
        requested scopes.
        """
        team_slugs = extract_team_scopes(scopes)

        # Validate team scopes - user must be member of each team
        if team_slugs:
            user = request.user
            user_team_slugs = set(user.teams.values_list("slug", flat=True))
            requested_team_slugs = set(team_slugs)

            if not requested_team_slugs.issubset(user_team_slugs):
                return False

        return True
