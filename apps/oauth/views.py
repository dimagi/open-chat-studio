from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

from apps.teams.helpers import get_default_team_from_request
from apps.teams.models import Membership, Team
from apps.teams.utils import set_current_team

from .forms import AuthorizationForm


class TeamScopedAuthorizationView(BaseAuthorizationView):
    """Authorization view that supports team-scoped OAuth access.

    The team can be specified via the 'team' URL parameter (optional).
    If not provided, defaults to the user's team on the current session.
    """

    form_class = AuthorizationForm
    template_name = "oauth2_provider/authorize.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.request.method == "GET":
            team_slug = self.request.GET.get("team")
            if team_slug and not Membership.objects.filter(team__slug=team_slug, user=self.request.user).exists():
                team_slug = None

            if not team_slug:
                team_slug = get_default_team_from_request(self.request).slug

            initial["team_slug"] = team_slug
        return initial

    def form_valid(self, form):
        # Set the team as thread context so the validator can pick it up
        set_current_team(Team.objects.get(slug=form.cleaned_data["team_slug"]))
        return super().form_valid(form)
