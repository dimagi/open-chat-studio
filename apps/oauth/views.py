from functools import cached_property

from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

from apps.teams.helpers import get_default_team_from_request
from apps.teams.models import Team
from apps.teams.utils import set_current_team

from .forms import AuthorizationForm


class TeamScopedAuthorizationView(BaseAuthorizationView):
    """Authorization view that supports team-scoped OAuth access.

    The team can be specified via the 'team' URL parameter (optional).
    If not provided, defaults to the user's team on the current session.
    """

    form_class = AuthorizationForm
    template_name = "oauth2_provider/authorize.html"

    @cached_property
    def requested_team(self):
        """Return the team requested via URL parameter, or None if not found or the user is not a member."""
        if team_slug := self.request.GET.get("team"):
            try:
                return self.request.user.teams.get(slug=team_slug)
            except Team.DoesNotExist:
                return None
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        requested_team = None
        if self.requested_team is not None:
            requested_team = self.requested_team

        context["requested_team"] = requested_team
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["team_requested"] = bool(self.requested_team)
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if (team := self.requested_team) or (team := get_default_team_from_request(self.request)):
            team_slug = team.slug
            # If no team found, team_slug remains None and the form will handle it.
        else:
            team_slug = None

        initial["team_slug"] = team_slug
        return initial

    def form_valid(self, form):
        # Set the team as thread context so the validator can pick it up
        set_current_team(Team.objects.get(slug=form.cleaned_data["team_slug"]))
        return super().form_valid(form)
