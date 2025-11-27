from django import forms
from oauth2_provider.forms import AllowForm


class AuthorizationForm(AllowForm):
    team_slug = forms.ChoiceField(label="Team", required=True)
    # Make the `scope` field not required, since it will be populated manually in the view
    scope = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, user, team_requested, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_slug"].choices = [(team.slug, team.name) for team in user.teams.all()]

        if team_requested:
            self.fields["team_slug"].widget = forms.HiddenInput()
            self.fields["team_slug"].disabled = True
