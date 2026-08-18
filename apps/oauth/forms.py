from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from oauth2_provider.forms import AllowForm

from apps.experiments.models import Experiment
from apps.oauth.models import OAuth2Application


class AuthorizationForm(AllowForm):
    team_slug = forms.ChoiceField(label="Team", required=True)
    # Make the `scope` field not required, since it will be populated manually in the view
    scope = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, user, application_team, team_requested, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if application_team:
            # The application is registered to a team, so there is nothing to choose: show the team but
            # don't let it be changed. The value of a disabled field comes from the form's initial data
            # (set by the view from the application), never from the POST payload.
            self.fields["team_slug"].choices = [(application_team.slug, application_team.name)]
            self.fields["team_slug"].disabled = True
            return

        self.fields["team_slug"].choices = [(team.slug, team.name) for team in user.teams.all()]
        if team_requested:
            self.fields["team_slug"].widget = forms.HiddenInput()
            self.fields["team_slug"].disabled = True

    def clean_team_slug(self):
        # Every path that populates this field is either user-supplied or derived from the application,
        # so membership is asserted here rather than at each of them.
        team_slug = self.cleaned_data["team_slug"]
        if not self.user.teams.filter(slug=team_slug).exists():
            raise forms.ValidationError(_("You are not a member of this team."))
        return team_slug


class RegisterApplicationForm(forms.ModelForm):
    """Register or edit an application scoped to a single team.

    The team is not a form field: it comes from the team in the URL (see `CreateApplication`) and the
    edit querysets are team-filtered, so an application can never be moved between teams.
    """

    # Only the two grant types OCS supports. Both issue tokens scoped to the application's team.
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

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["algorithm"].disabled = True
        # redirect_uris is only required for the authorization-code grant; enforced in clean().
        self.fields["redirect_uris"].required = False

        if self.instance.pk:
            self.fields["client_secret"].widget = forms.HiddenInput()
            # The grant type re-scopes every token issued by this application, so it is immutable once
            # the application exists.
            self.fields["authorization_grant_type"].disabled = True

        if "allowed_chatbots" in self.fields:
            self._init_allowed_chatbots(team)

    def _init_allowed_chatbots(self, team):
        """Offer the team's chatbots, plus whatever this application already points at.

        A ModelMultipleChoiceField silently drops selected values that fall outside its queryset, so
        filtering archived chatbots out on its own would quietly revoke an archived one on the next
        unrelated save. The current selections are unioned back in -- and set as the initial value,
        since the related manager the ModelForm reads them through excludes archived rows too.
        """
        selected_ids = list(
            Experiment.objects.get_all().filter(oauth_applications=self.instance).values_list("pk", flat=True)
            if self.instance.pk
            else []
        )
        offered = Q(team=team, working_version__isnull=True, is_archived=False) if team else Q(pk__in=[])
        field = self.fields["allowed_chatbots"]
        field.queryset = Experiment.objects.get_all().filter(offered | Q(pk__in=selected_ids)).order_by("name")
        self.initial["allowed_chatbots"] = selected_ids

    def clean(self):
        cleaned_data = super().clean()
        grant_type = cleaned_data.get("authorization_grant_type")
        if grant_type == OAuth2Application.GRANT_AUTHORIZATION_CODE and not cleaned_data.get("redirect_uris"):
            self.add_error("redirect_uris", "Redirect URIs are required for authorization-code applications.")
        if grant_type != OAuth2Application.GRANT_CLIENT_CREDENTIALS and "allowed_chatbots" in cleaned_data:
            # Only client-credentials applications are pinned to chatbots; an authorization-code token
            # carries a user, so it keeps team-membership semantics and a stored list would mislead.
            cleaned_data["allowed_chatbots"] = Experiment.objects.none()
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
            # `super().save(commit=False)` defers the m2m write to here; without this
            # `allowed_chatbots` would never persist.
            self.save_m2m()
        return instance

    class Meta:
        model = OAuth2Application
        fields = [
            "name",
            "client_id",
            "client_secret",
            "authorization_grant_type",
            "redirect_uris",
            "post_logout_redirect_uris",
            "allowed_origins",
            "algorithm",
            "allowed_chatbots",
        ]
        widgets = {
            "allowed_chatbots": forms.CheckboxSelectMultiple,
        }
        help_texts = {
            "redirect_uris": "Enter one URI per line. These are the allowed redirect URIs after authorization.",
            "post_logout_redirect_uris": "Enter one URI per line. Optional URIs for post-logout redirects.",
            "allowed_origins": "Enter one origin per line. Optional CORS allowed origins.",
            "algorithm": "Algorithm for signing tokens.",
            "allowed_chatbots": (
                "Chatbots this application may start chat sessions with. Empty means none. Applies to "
                "client-credentials applications only."
            ),
        }


class RegisterGlobalApplicationForm(RegisterApplicationForm):
    """Register or edit a global (team-less) application. Superusers only.

    Global applications must use the authorization-code grant: their tokens are scoped to the team the
    authorizing user selects, and a client-credentials application has no such user to select one.
    """

    authorization_grant_type = forms.ChoiceField(
        choices=[(OAuth2Application.GRANT_AUTHORIZATION_CODE, "Authorization code")],
        label="Grant type",
        help_text="Global applications support the authorization-code grant only.",
    )

    class Meta(RegisterApplicationForm.Meta):
        # No allowed_chatbots: the field only means anything for client-credentials applications, and
        # a global application has no team whose chatbots could be offered.
        fields = [field for field in RegisterApplicationForm.Meta.fields if field != "allowed_chatbots"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["authorization_grant_type"].disabled = True
        if not self.instance.pk:
            # Only force the grant type when registering. A disabled field takes its value from the
            # initial data, so doing this on edit would silently rewrite the stored grant type -- and the
            # grant type re-scopes every token the application issues.
            self.initial["authorization_grant_type"] = OAuth2Application.GRANT_AUTHORIZATION_CODE
