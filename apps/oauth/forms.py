from django import forms
from oauth2_provider.forms import AllowForm

from apps.oauth.models import OAuth2Application


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


class RegisterApplicationForm(forms.ModelForm):
    name = forms.CharField(required=True, max_length=255)

    algorithm = forms.ChoiceField(
        choices=[("", "No algorithm"), ("RS256", "RS256")],
        required=False,
        help_text="Algorithm for signing tokens. Leave empty for no signing or select RS256 for RSA signing.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["authorization_grant_type"].disabled = True
        self.fields["redirect_uris"].required = True
        if self.instance.pk:
            self.fields["client_secret"].required = False
            self.fields[
                "client_secret"
            ].help_text = "Leave blank to keep the existing secret. Enter a new value to change it."

    def clean_client_secret(self):
        """Handle optional client_secret for updates."""
        client_secret = self.cleaned_data.get("client_secret")
        # If updating and client_secret is empty, don't change it
        if self.instance.pk and not client_secret:
            # Return the existing value so it doesn't get cleared
            return self.instance.client_secret
        return client_secret

    def save(self, commit=True):
        # Force these fields to specific values
        instance = super().save(commit=False)
        instance.authorization_grant_type = OAuth2Application.GRANT_AUTHORIZATION_CODE
        instance.client_type = OAuth2Application.CLIENT_CONFIDENTIAL
        instance.hash_client_secret = True
        instance.skip_authorization = False
        if commit:
            instance.save()
        return instance

    class Meta:
        model = OAuth2Application
        fields = [
            "name",
            "client_id",
            "client_secret",
            "redirect_uris",
            "post_logout_redirect_uris",
            "allowed_origins",
            "algorithm",
            "authorization_grant_type",
        ]
        help_texts = {
            "redirect_uris": "Enter one URI per line. These are the allowed redirect URIs after authorization.",
            "post_logout_redirect_uris": "Enter one URI per line. Optional URIs for post-logout redirects.",
            "allowed_origins": "Enter one origin per line. Optional CORS allowed origins.",
            "algorithm": "Algorithm for signing tokens. Leave empty for no signing or select RS256 for RSA signing.",
        }
