from django import forms
from oauth2_provider.forms import AllowForm


class TeamScopedAllowForm(AllowForm):
    team_slug = forms.ChoiceField(label="Team", required=True)
    # Make the `scope` field not required, since it will be populated manually in the view
    scope = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_slug"].choices = [(team.slug, team.name) for team in user.teams.all()]
