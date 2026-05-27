import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.mark.django_db()
class TestProfileEmailUpdate:
    def test_changing_email_to_already_verified_address_promotes_it_to_primary(self, client, team_with_users):
        """When a user changes their email to one they already own and have verified
        (a secondary EmailAddress), that EmailAddress should become primary and the
        previous primary should be demoted."""
        user = team_with_users.members.first()
        old_email = "old@example.com"
        new_email = "new@example.com"
        user.email = old_email
        user.save()

        EmailAddress.objects.create(user=user, email=old_email, verified=True, primary=True)
        EmailAddress.objects.create(user=user, email=new_email, verified=True, primary=False)

        client.force_login(user)
        response = client.post(
            reverse("users:user_profile"),
            {"email": new_email, "first_name": user.first_name, "last_name": user.last_name},
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.email == new_email
        assert EmailAddress.objects.get(user=user, email=new_email).primary is True
        assert EmailAddress.objects.get(user=user, email=old_email).primary is False

    def test_changing_email_with_no_existing_email_address_does_not_error(self, client, team_with_users):
        """If the user has no EmailAddress row for the new email (and confirmation is not required),
        the view should still succeed without trying to set a non-existent address as primary."""
        user = team_with_users.members.first()
        user.email = "old@example.com"
        user.save()

        client.force_login(user)
        response = client.post(
            reverse("users:user_profile"),
            {"email": "brand-new@example.com", "first_name": user.first_name, "last_name": user.last_name},
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.email == "brand-new@example.com"
        assert not EmailAddress.objects.filter(user=user, email="brand-new@example.com").exists()

    def test_unchanged_email_does_not_touch_email_address_rows(self, client, team_with_users):
        """If the email isn't changing, the EmailAddress rows should be left alone (in particular,
        we shouldn't be calling set_as_primary)."""
        user = team_with_users.members.first()
        email = user.email
        primary = EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        secondary = EmailAddress.objects.create(user=user, email="other@example.com", verified=True, primary=False)

        client.force_login(user)
        response = client.post(
            reverse("users:user_profile"),
            {"email": email, "first_name": "Updated", "last_name": user.last_name},
        )

        assert response.status_code == 200
        primary.refresh_from_db()
        secondary.refresh_from_db()
        assert primary.primary is True
        assert secondary.primary is False
