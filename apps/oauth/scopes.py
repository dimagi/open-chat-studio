from oauth2_provider.scopes import BaseScopes

from apps.teams.models import Team


class TeamScopedScopesBackend(BaseScopes):
    """
    This class provides dynamic scopes based on the teams the user belongs to.
    """

    def get_all_scopes(self):
        """Return all possible team scopes."""
        scopes = {}
        for slug in Team.objects.values_list("slug", flat=True):
            scopes[f"team:{slug}"] = f"Access data from team {slug}"

        return scopes

    def get_available_scopes(self, application=None, request=None, *args, **kwargs):
        """Return available team scopes for the authenticated user."""
        scopes = []
        if request and request.user.is_authenticated:
            team_scopes = [f"team:{team.slug}" for team in request.user.teams.all()]
            scopes.extend(team_scopes)
        return scopes

    def get_default_scopes(self, application=None, request=None, *args, **kwargs):
        return []
