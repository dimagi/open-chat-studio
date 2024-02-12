from django.test import TestCase
from django.urls import reverse

from apps.teams.backends import add_user_to_team, make_user_team_owner
from apps.teams.models import Invitation, Team
from apps.teams.roles import ROLE_MEMBER
from apps.users.models import CustomUser

PASSWORD = "123"


class TeamsAuthTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sox = Team.objects.create(name="Red Sox", slug="sox")
        cls.yanks = Team.objects.create(name="Yankees", slug="yanks")

        cls.sox_admin = _create_user("tito@redsox.com")
        make_user_team_owner(cls.sox, cls.sox_admin)

        cls.yanks_member = _create_user("derek.jeter@yankees.com")
        add_user_to_team(cls.yanks, cls.yanks_member)

    def test_unauthenticated_view(self):
        response = self.client.get(reverse("web:home"))
        assert 200 == response.status_code
        self._assertRequestHasTeam(response, None)

    def test_authenticated_non_team_view(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("users:user_profile"))
        assert 200 == response.status_code, response
        self._assertRequestHasTeam(response, self.sox, self.sox_admin)

    def test_team_view(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("single_team:manage_team", args=[self.sox.slug]))
        assert 200 == response.status_code
        self._assertRequestHasTeam(response, self.sox, self.sox_admin)

    def test_team_view_no_membership(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("single_team:manage_team", args=[self.yanks.slug]))
        assert 404 == response.status_code
        self._assertRequestHasTeam(response, self.yanks, None)

    def test_team_view_missing_team(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("single_team:manage_team", args=["missing"]))
        assert 404 == response.status_code
        self._assertRequestHasTeam(response, None, None)

    def test_team_admin_view(self):
        self._login(self.sox_admin)
        invite = self._create_invitation()
        response = self.client.post(reverse("single_team:resend_invitation", args=[self.sox.slug, invite.id]))
        assert 200 == response.status_code
        self._assertRequestHasTeam(response, self.sox, self.sox_admin)

    def test_team_admin_view_denied(self):
        self._login(self.yanks_member)
        invite = self._create_invitation()
        response = self.client.post(reverse("single_team:resend_invitation", args=[self.yanks.slug, invite.id]))
        assert 403 == response.status_code
        self._assertRequestHasTeam(response, self.yanks, self.yanks_member)

    def _login(self, user):
        success = self.client.login(username=user.username, password="123")
        assert success, f"User login failed: {user.username}"

    def _create_invitation(self):
        return Invitation.objects.create(
            team=self.sox, email="dj@yankees.com", role=ROLE_MEMBER, invited_by=self.sox_admin
        )

    def _assertRequestHasTeam(self, response, team, user=None):
        request = response.wsgi_request
        assert hasattr(request, "team")
        assert request.team == team
        assert hasattr(request, "team_membership")
        membership = request.team_membership
        if user:
            assert membership.user == user
        else:
            # use equality check to force setup of the lazy object
            assert membership == None  # noqa E711


def _create_user(username):
    user = CustomUser.objects.create(username=username)
    user.set_password(PASSWORD)
    user.save()
    return user
