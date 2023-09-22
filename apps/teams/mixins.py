from django.utils.decorators import method_decorator

from apps.teams.decorators import login_and_team_required, team_admin_required


class TeamObjectViewMixin:
    """
    Abstract model for Django class-based views for a model that belongs to a Team
    """

    def get_queryset(self):
        """Narrow queryset to only include objects of this team."""
        return self.model.objects.filter(team=self.request.team)


class LoginAndTeamRequiredMixin(TeamObjectViewMixin):
    """
    Verify that the current user is authenticated and a member of the team.
    """

    @method_decorator(login_and_team_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class TeamAdminRequiredMixin(TeamObjectViewMixin):
    """
    Verify that the current user is authenticated and admin of the team.
    """

    @method_decorator(team_admin_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)
