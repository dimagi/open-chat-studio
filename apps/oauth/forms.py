from django import forms
from oauth2_provider.forms import AllowForm
from oauth2_provider.scopes import get_scopes_backend


class TeamScopedAllowForm(AllowForm):
    team = forms.ChoiceField(required=True)
    # Make the `scope` field not required, since it will be populated manually in the view
    scope = forms.CharField(widget=forms.HiddenInput(), required=False)
    apis = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=True)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].choices = [(team.slug, team.name) for team in user.teams.all()]
        self.fields["apis"].choices = list(get_scopes_backend().get_all_scopes().items())

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["scope"] = " ".join(cleaned_data["apis"])
        return cleaned_data
