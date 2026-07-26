import pytest
from django.core import mail

from apps.teams.export.emails import send_password_reset_email
from apps.users.models import CustomUser

pytestmark = pytest.mark.django_db


def test_send_password_reset_email_sends_one_email_to_the_user():
    user = CustomUser.objects.create(username="newbie@example.com", email="newbie@example.com")
    mail.outbox.clear()

    send_password_reset_email(user)

    assert len(mail.outbox) == 1
    assert "newbie@example.com" in mail.outbox[0].to


def test_send_password_reset_email_is_a_noop_without_an_email():
    user = CustomUser.objects.create(username="no-email-user", email="")
    mail.outbox.clear()

    send_password_reset_email(user)

    assert mail.outbox == []
