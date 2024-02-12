from allauth.account.forms import SignupForm
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .helpers import create_default_team_for_user
from .models import Invitation, Membership, Team


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
                )

            if invite.is_accepted:
                raise forms.ValidationError(
                    _(
                        "It looks like that invitation link has expired. "
                        "Please request a new invitation or sign in to continue."
                    )
                )

            email = cleaned_data.get("email")
            if invite.email != email:
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


class TeamChangeForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "slug")
        labels = {
            "name": _("Team Name"),
            "slug": _("Team ID"),
        }
        help_texts = {
            "name": _("Your team name."),
            "slug": _("A unique ID for your team. No spaces are allowed!"),
        }


class InvitationForm(forms.ModelForm):
    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_email(self):
        email = self.cleaned_data["email"]
        # confirm no other pending invitations for this email
        if Invitation.objects.filter(team=self.team, email=email, is_accepted=False):
            raise ValidationError(
                _(
                    'There is already a pending invitation for {}. You can resend it by clicking "Resend Invitation".'
                ).format(email)
            )

        return email

    class Meta:
        model = Invitation
        fields = ("email", "groups")


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ("groups",)
