import uuid

from django.test import TestCase
from django.urls import reverse

from apps.users.models import CustomUser

from ..forms import InvitationForm, TeamSignupForm
from ..models import Invitation, Membership, Team


class AcceptInvitationViewTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Test Team", slug="test-team")
        self.user = CustomUser.objects.create_user(username="testuser", password="12345")
        self.invitation = Invitation.objects.create(
            id=uuid.uuid4(),
            email="test@example.com",
            team=self.team,
            is_accepted=False,
            invited_by=self.user,
        )

    def test_accept_invitation_loads(self):
        response = self.client.get(reverse("teams:accept_invitation", args=[self.invitation.id]))
        assert response.status_code == 200

    def test_user_email_matches_case_insensitive(self):
        """Users with email in different case than the invitation should still match."""
        CustomUser.objects.create_user(username="upperuser", email="TEST@EXAMPLE.COM", password="12345")
        self.client.login(username="upperuser", password="12345")
        response = self.client.get(reverse("teams:accept_invitation", args=[self.invitation.id]))
        assert response.status_code == 200
        assert response.context["user_email_matches"] is True


class InvitationFormTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Test Team", slug="test-team")
        self.inviter = CustomUser.objects.create_user(username="inviter", email="inviter@example.com", password="12345")

    def test_email_is_normalized_to_lowercase(self):
        """Invitation emails should be stored in lowercase."""
        form = InvitationForm(
            team=self.team,
            data={"email": "User@Example.COM", "groups": []},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email"] == "user@example.com"

    def test_duplicate_invitation_check_is_case_insensitive(self):
        """A pending invitation for the same email in different case should be rejected."""
        Invitation.objects.create(
            team=self.team,
            email="user@example.com",
            invited_by=self.inviter,
            is_accepted=False,
        )
        form = InvitationForm(
            team=self.team,
            data={"email": "USER@EXAMPLE.COM", "groups": []},
        )
        assert not form.is_valid()
        assert "email" in form.errors

    def test_existing_member_check_is_case_insensitive(self):
        """Inviting an existing member with a different email case should be rejected."""
        existing_user = CustomUser.objects.create_user(
            username="existing", email="member@example.com", password="12345"
        )
        Membership.objects.create(team=self.team, user=existing_user)
        form = InvitationForm(
            team=self.team,
            data={"email": "MEMBER@EXAMPLE.COM", "groups": []},
        )
        assert not form.is_valid()
        assert "email" in form.errors


class TeamSignupFormInvitationEmailTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Test Team", slug="test-team")
        self.inviter = CustomUser.objects.create_user(username="inviter", email="inviter@example.com", password="12345")
        self.invitation = Invitation.objects.create(
            team=self.team,
            email="invited@example.com",
            invited_by=self.inviter,
            is_accepted=False,
        )

    def _make_form_data(self, email):
        return {
            "email": email,
            "password1": "strong-password-123!",
            "password2": "strong-password-123!",
            "username": email,
            "invitation_id": str(self.invitation.id),
            "terms_agreement": True,
        }

    def test_signup_with_matching_email_case(self):
        """Signup with the exact invitation email should pass validation."""
        form = TeamSignupForm(data=self._make_form_data("invited@example.com"))
        assert form.is_valid(), form.errors

    def test_signup_with_different_email_case(self):
        """Signup with a different case of the invitation email should pass validation."""
        form = TeamSignupForm(data=self._make_form_data("INVITED@EXAMPLE.COM"))
        assert form.is_valid(), form.errors
