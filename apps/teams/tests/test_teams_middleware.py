from django.http import Http404
from django.test import TestCase
from django.urls import reverse

from apps.teams.models import Invitation, Team
from apps.teams.roles import ROLE_ADMIN, ROLE_MEMBER
from apps.users.models import CustomUser

PASSWORD = "123"


class TeamsAuthTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sox = Team.objects.create(name="Red Sox", slug="sox")
        cls.yanks = Team.objects.create(name="Yankees", slug="yanks")

        cls.sox_admin = _create_user("tito@redsox.com")
        cls.sox.members.add(cls.sox_admin, through_defaults={"role": ROLE_ADMIN})

        cls.yanks_member = _create_user("derek.jeter@yankees.com")
        cls.yanks.members.add(cls.yanks_member, through_defaults={"role": ROLE_MEMBER})

    def test_unauthenticated_view(self):
        response = self.client.get(reverse("web:home"))
        self.assertEqual(200, response.status_code)
        self._assertRequestHasTeam(response, None)

    def test_authenticated_non_team_view(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("users:user_profile"))
        self.assertEqual(200, response.status_code, response)
        self._assertRequestHasTeam(response, self.sox, self.sox_admin, ROLE_ADMIN)

    def test_team_view(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("single_team:manage_team", args=[self.sox.slug]))
        self.assertEqual(200, response.status_code)
        self._assertRequestHasTeam(response, self.sox, self.sox_admin, ROLE_ADMIN)

    def test_team_view_no_membership(self):
        self._login(self.sox_admin)
        response = self.client.get(reverse("single_team:manage_team", args=[self.yanks.slug]))
        self.assertEqual(404, response.status_code)
        self._assertRequestHasTeam(response, self.yanks, None, None)

    def test_team_admin_view(self):
        self._login(self.sox_admin)
        invite = self._create_invitation()
        response = self.client.post(reverse("single_team:resend_invitation", args=[self.sox.slug, invite.id]))
        self.assertEqual(200, response.status_code)
        self._assertRequestHasTeam(response, self.sox, self.sox_admin, ROLE_ADMIN)

    def test_team_admin_view_denied(self):
        self._login(self.yanks_member)
        invite = self._create_invitation()
        response = self.client.post(reverse("single_team:resend_invitation", args=[self.yanks.slug, invite.id]))
        self.assertEqual(404, response.status_code)
        self._assertRequestHasTeam(response, self.yanks, self.yanks_member, ROLE_MEMBER)

    def _login(self, user):
        success = self.client.login(username=user.username, password="123")
        self.assertTrue(success, f"User login failed: {user.username}")

    def _create_invitation(self):
        return Invitation.objects.create(
            team=self.sox, email="dj@yankees.com", role=ROLE_MEMBER, invited_by=self.sox_admin
        )

    def _assertRequestHasTeam(self, response, team, user=None, role=None):
        request = response.wsgi_request
        self.assertTrue(hasattr(request, "team"))
        self.assertEqual(request.team, team)
        self.assertTrue(hasattr(request, "team_membership"))
        membership = request.team_membership
        if user or role:
            self.assertEqual(membership.user, user)
            self.assertEqual(membership.role, role)
        else:
            # use assertEqual to force setup of the lazy object
            self.assertEqual(membership, None)


def _create_user(username):
    user = CustomUser.objects.create(username=username)
    user.set_password(PASSWORD)
    user.save()
    return user
