import uuid

from django.test import TestCase
from django.urls import reverse

from apps.users.models import CustomUser

from ..models import Invitation, Team


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
