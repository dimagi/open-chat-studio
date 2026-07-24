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
    # Only the two grant types OCS supports. Authorization-code scopes the token to a team chosen
    # interactively at authorization time; client-credentials pins the team on the application here.
    GRANT_TYPE_CHOICES = [
        (OAuth2Application.GRANT_AUTHORIZATION_CODE, "Authorization code"),
        (OAuth2Application.GRANT_CLIENT_CREDENTIALS, "Client credentials"),
    ]

    name = forms.CharField(required=True, max_length=255)

    authorization_grant_type = forms.ChoiceField(
        choices=GRANT_TYPE_CHOICES,
        label="Grant type",
        help_text="Authorization code for user-facing apps; client credentials for machine-to-machine access.",
    )

    algorithm = forms.ChoiceField(
        choices=[("RS256", "RS256")],
        required=False,
        help_text="Algorithm for signing JWT tokens.",
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        self.fields["algorithm"].disabled = True
        self.fields["team"].queryset = self.user.teams.all()
        # redirect_uris / team are conditionally required depending on the grant type; enforced in clean().
        self.fields["redirect_uris"].required = False
        self.fields["team"].required = False

        if self.instance.pk:
            self.fields["client_secret"].widget = forms.HiddenInput()
            # Both the grant type and the pinned team re-scope every token issued by this application,
            # so they are immutable once the application exists.
            self.fields["authorization_grant_type"].disabled = True
            self.fields["team"].disabled = True

    def clean(self):
        cleaned_data = super().clean()
        grant_type = cleaned_data.get("authorization_grant_type")
        if grant_type == OAuth2Application.GRANT_CLIENT_CREDENTIALS:
            if not cleaned_data.get("team"):
                self.add_error("team", "A team is required for client-credentials applications.")
        elif grant_type == OAuth2Application.GRANT_AUTHORIZATION_CODE:
            if not cleaned_data.get("redirect_uris"):
                self.add_error("redirect_uris", "Redirect URIs are required for authorization-code applications.")
        return cleaned_data

    def save(self, commit=True):
        # Force these fields to specific values
        instance = super().save(commit=False)
        instance.algorithm = OAuth2Application.RS256_ALGORITHM
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
            "authorization_grant_type",
            "team",
            "redirect_uris",
            "post_logout_redirect_uris",
            "allowed_origins",
            "algorithm",
        ]
        help_texts = {
            "team": "The team this application's tokens are scoped to (client-credentials only).",
            "redirect_uris": "Enter one URI per line. These are the allowed redirect URIs after authorization.",
            "post_logout_redirect_uris": "Enter one URI per line. Optional URIs for post-logout redirects.",
            "allowed_origins": "Enter one origin per line. Optional CORS allowed origins.",
            "algorithm": "Algorithm for signing tokens.",
        }
