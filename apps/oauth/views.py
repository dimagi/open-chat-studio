from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

from apps.teams.models import Team
from apps.teams.utils import set_current_team

from .forms import TeamScopedAllowForm


class TeamScopedAuthorizationView(BaseAuthorizationView):
    form_class = TeamScopedAllowForm
    template_name = "oauth2_provider/authorize.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        selected_team_slug = form.cleaned_data.get("team", [])
        # Set the team as thread context so the validator can pick it up
        set_current_team(Team.objects.get(slug=selected_team_slug))
        return super().form_valid(form)
