from allauth.account.forms import SignupForm
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .flags import get_all_flag_info
from .helpers import create_default_team_for_user
from .metadata import get_team_metadata_fields
from .models import Flag, Invitation, Membership, Team


class TeamSignupForm(SignupForm):
    invitation_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    team_name = forms.CharField(
        label=_("Team Name (Optional)"),
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": _("Team Name (Optional)")}),
        required=False,
    )
    terms_agreement = forms.BooleanField(required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.PROJECT_METADATA.get("TERMS_URL"):
            link = '<a href={} target="_blank">{}</a>'.format(
                settings.PROJECT_METADATA["TERMS_URL"],
                _("Terms and Conditions"),
            )
            self.fields["terms_agreement"].label = mark_safe(_("I agree to the {terms_link}").format(terms_link=link))
        else:
            del self.fields["terms_agreement"]

        if "email" in kwargs.get("initial", {}):
            self.fields["email"].widget.attrs = {"readonly": "readonly"}

    def clean(self):
        cleaned_data = super().clean()
        if not self.errors:
            self._clean_team_name(cleaned_data)
            self._clean_invitation_email(cleaned_data)
        return cleaned_data

    def _clean_team_name(self, cleaned_data):
        team_name = cleaned_data.get("team_name")
        invitation_id = cleaned_data.get("invitation_id")
        # if invitation is not set then team name is required
        if not invitation_id and not team_name:
            email = cleaned_data.get("email")
            if email is not None:
                team_name = f"{email.split('@')[0]}"
        elif invitation_id:
            assert not team_name

        cleaned_data["team_name"] = team_name

    def _clean_invitation_email(self, cleaned_data):
        invitation_id = cleaned_data.get("invitation_id")
        if invitation_id:
            try:
                invite = Invitation.objects.get(id=invitation_id)
            except (Invitation.DoesNotExist, ValidationError):
                # ValidationError is raised if the ID isn't a valid UUID, which should be treated the same
                # as not found
                raise forms.ValidationError(
                    _(
                        "That invitation could not be found. "
                        "Please double check your invitation link or sign in to continue."
                    )
                ) from None

            if invite.is_accepted:
                raise forms.ValidationError(
                    _(
                        "The invitation has already been accepted. "
                        "Please sign in to continue or request a new invitation."
                    )
                )

            email = cleaned_data.get("email")
            if invite.email.lower() != email.lower():
                raise forms.ValidationError(
                    _("You must sign up with the email address that the invitation was sent to.")
                )

    def save(self, request):
        invitation_id = self.cleaned_data["invitation_id"]
        team_name = self.cleaned_data["team_name"]
        user = super().save(request)

        if not invitation_id:
            create_default_team_for_user(user, team_name)

        return user


def _build_metadata_field(field: dict, initial: str) -> forms.Field:
    field_type = field["type"]
    if field_type == "select":
        choices = [("", "---------")] + [(option, option) for option in field["options"]]
        return forms.ChoiceField(label=field["label"], required=False, initial=initial, choices=choices)
    if field_type == "email":
        return forms.EmailField(label=field["label"], required=False, initial=initial)
    return forms.CharField(label=field["label"], required=False, initial=initial)


class TeamMetadataForm(forms.Form):
    """Edit a team's internal (staff-only) metadata.

    Fields are built dynamically from the configured ``TEAM_METADATA_FIELDS`` (honouring
    each field's ``type``: text, email or select) so the form stays in sync with the
    setting. Values are stored as stripped strings in the team's ``metadata`` JSON. Used
    by both the per-team settings page and the global admin team-detail page.
    """

    def __init__(self, *args, team: Team, **kwargs):
        self.team = team
        super().__init__(*args, **kwargs)
        metadata = team.metadata or {}
        for field in get_team_metadata_fields():
            key = field["key"]
            self.fields[key] = _build_metadata_field(field, initial=metadata.get(key, ""))

    def save(self):
        metadata = dict(self.team.metadata or {})
        metadata.update({key: (value or "").strip() for key, value in self.cleaned_data.items()})
        self.team.metadata = metadata
        self.team.save(update_fields=["metadata"])
        return self.team


class TeamChangeForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name",)
        labels = {
            "name": _("Team Name"),
        }
        help_texts = {
            "name": _("Your team name."),
        }


class TeamPublicKeyForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("public_key", "is_migrating")
        labels = {
            "public_key": _("Public Key"),
            "is_migrating": _("Migration mode"),
        }
        help_texts = {
            "public_key": _("Public key used to seal data exported from this team."),
            "is_migrating": _(
                "Freeze this team's outbound message firing while its data is migrated to another server."
            ),
        }
        widgets = {
            "public_key": forms.Textarea(attrs={"rows": 4, "placeholder": "-----BEGIN PUBLIC KEY-----"}),
        }

    def clean_public_key(self):
        value = self.cleaned_data.get("public_key", "")
        if not value:
            return value
        try:
            load_pem_public_key(value.encode())
        except (ValueError, UnsupportedAlgorithm) as e:
            raise ValidationError(_("Enter a valid PEM-encoded public key.")) from e
        return value


class TeamMigrationForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("is_migrating",)
        labels = {"is_migrating": _("Migration mode")}
        help_texts = {
            "is_migrating": _(
                "Freeze this team's outbound message firing while its data is migrated to another server."
            ),
        }


class TeamMfaForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("require_mfa",)
        labels = {"require_mfa": _("Require two-factor authentication")}
        help_texts = {
            "require_mfa": _("Every member of this team must enrol in two-factor authentication to keep access."),
        }


class NotifyRecipientsForm(forms.Form):
    NOTIFICATION_CHOICES = [
        ("self", "Send email notification to myself"),
        ("admins", "Send email notification to admins"),
        ("all", "Send email notification to all members of the team"),
    ]

    notification_recipients = forms.ChoiceField(choices=NOTIFICATION_CHOICES, widget=forms.Select)


class InvitationForm(forms.ModelForm):
    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_email(self):
        email = self.cleaned_data["email"]
        if Membership.objects.filter(team=self.team, user__email__iexact=email).exists():
            raise ValidationError(_("A user with that email is already a member of this team."))

        # confirm no other pending invitations for this email
        if Invitation.objects.filter(team=self.team, email__iexact=email, is_accepted=False).exists():
            raise ValidationError(
                _(
                    'There is already a pending invitation for {}. You can resend it by clicking "Resend Invitation".'
                ).format(email)
            )

        return email.lower()

    class Meta:
        model = Invitation
        fields = ("email", "groups")
        widgets = {
            "groups": forms.CheckboxSelectMultiple(),
        }


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ("groups",)
        widgets = {
            "groups": forms.CheckboxSelectMultiple(),
        }


class FeatureFlagForm(forms.Form):
    """Form for managing team feature flags."""

    def __init__(self, *args, **kwargs):
        self.team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)

        self._all_flags = None

        flag_info = get_all_flag_info()

        for flag_name, info in flag_info.items():
            if not info.teams_can_manage:
                continue

            label = flag_name.split("_", 1)[-1]
            help_text = f"Flag: {label}"
            if info.docs_slug:
                help_text += format_html(' (<a class="link" target="_blank" href="{}">docs</a>)', info.docs_url)
            self.fields[flag_name] = forms.BooleanField(
                label=info.description,
                required=False,
                help_text=mark_safe(help_text),
                initial=self._is_flag_active_for_team(flag_name),
            )

    def _is_flag_active_for_team(self, flag_name):
        """Check if a flag is active for the current team."""
        if not self.team:
            return False

        try:
            flag = Flag.objects.get(name=flag_name)
            return flag.is_active_for_team(self.team)
        except Flag.DoesNotExist:
            return False

    def save(self):
        """Save the form by updating team flag associations."""
        if not self.team:
            return

        flag_infos = get_all_flag_info()

        for flag_name, is_enabled in self.cleaned_data.items():
            flag_info = flag_infos.get(flag_name)
            if not flag_info or not flag_info.teams_can_manage:
                continue
            if is_enabled == self.fields[flag_name].initial:
                # A flag on through `everyone` renders ticked; writing the team into the M2M
                # on an unrelated save would keep the feature past the end of the rollout.
                continue

            flag = self._get_or_create_flag(flag_name)
            if is_enabled:
                flag.teams.add(self.team)
                # Auto-enable required flags
                for required_flag_name in flag_info.requires:
                    required_flag = self._get_or_create_flag(required_flag_name)
                    required_flag.teams.add(self.team)
            else:
                flag.teams.remove(self.team)

            # Clear the cache to ensure the flag state is updated
            flag.flush()

    def _get_or_create_flag(self, flag_name):
        if not self._all_flags:
            self._all_flags = {flag.name: flag for flag in Flag.get_all()}

        if flag_name not in self._all_flags:
            flag, _created = Flag.objects.get_or_create(
                name=flag_name, defaults={"everyone": None, "superusers": False}
            )
            self._all_flags[flag_name] = flag
        return self._all_flags[flag_name]
