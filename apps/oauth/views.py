from oauth2_provider.views.base import AuthorizationView as BaseAuthorizationView

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
        # Encode as team scopes
        # Override scope with custom team scopes
        form.cleaned_data["scope"] = f"team:{selected_team_slug}"
        return super().form_valid(form)
