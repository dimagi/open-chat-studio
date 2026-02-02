from base64 import b64decode
from unittest.mock import patch

import pytest

from apps.ocs_notifications.models import LevelChoices, Notification, UserNotification, UserNotificationPreferences
from apps.ocs_notifications.utils import (
    bust_unread_notification_cache,
    create_identifier,
    create_notification,
    send_notification_email,
)
from apps.utils.factories.notifications import UserNotificationFactory


@pytest.mark.django_db()
def test_email_not_sent_when_preference_doesnt_exist(team_with_users, mailoutbox):
    """
    Test that email is not sent when user notification preferences don't exist.
    """
    user = team_with_users.members.first()
    user_notification = UserNotificationFactory.create(user=user, notification__level=LevelChoices.ERROR)

    send_notification_email(user_notification)
    assert len(mailoutbox) == 0


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("notification_level", "email_preference_level", "should_send"),
    [
        # When preference is INFO, send all types
        (LevelChoices.INFO, LevelChoices.INFO, True),
        (LevelChoices.WARNING, LevelChoices.INFO, True),
        (LevelChoices.ERROR, LevelChoices.INFO, True),
        # When preference is WARNING, only send WARNING and ERROR
        (LevelChoices.INFO, LevelChoices.WARNING, False),
        (LevelChoices.WARNING, LevelChoices.WARNING, True),
        (LevelChoices.ERROR, LevelChoices.WARNING, True),
        # When preference is ERROR, only send ERROR
        (LevelChoices.INFO, LevelChoices.ERROR, False),
        (LevelChoices.WARNING, LevelChoices.ERROR, False),
        (LevelChoices.ERROR, LevelChoices.ERROR, True),
    ],
)
def test_send_notification_email_respects_levels(
    team_with_users, notification_level, email_preference_level, should_send, mailoutbox
):
    """
    Test that email notifications respect user notification level preferences.

    Verifies that emails are sent only when the notification level meets or exceeds
    the user's email notification threshold level.
    """
    user = team_with_users.members.first()
    user_notification = UserNotificationFactory.create(user=user, notification__level=notification_level)

    # Create user preferences with email level threshold
    UserNotificationPreferences.objects.create(
        team=user_notification.team,
        user=user,
        email_enabled=True,
        email_level=email_preference_level,
    )

    send_notification_email(user_notification)

    if should_send:
        assert len(mailoutbox) == 1
    else:
        assert len(mailoutbox) == 0


@pytest.mark.django_db()
class TestCreateNotification:
    def test_create_notification_notifies_user_when_notification_created(self, team_with_users):
        """
        Test that creating a new notification notifies the recipient user.

        Verifies that:
        1. A UserNotification entry is created for the specified user
        2. The user's unread notification cache is invalidated
        """
        user = team_with_users.members.first()
        assert UserNotification.objects.filter(user=user).count() == 0

        create_notification(
            title="Test Notification", message="Test message", level=LevelChoices.INFO, team=team_with_users
        )

        assert UserNotification.objects.filter(user=user).count() == 1

    @patch("apps.ocs_notifications.utils.bust_unread_notification_cache", wraps=bust_unread_notification_cache)
    def test_user_is_notified_again(self, mock_bust_cache, team_with_users):
        """
        Test that reading a notification allows the user to be renotified.

        Verifies that:
        1. A user can be renotified for the same notification (same identifier)
        2. After marking a notification as read, creating the same notification
           again resets it to unread
        3. The cache is busted upon renotification
        """
        # Create initial notification
        create_notification(
            title="Test Notification", message="Test message", level=LevelChoices.ERROR, team=team_with_users
        )

        # Get the notification and mark it as read
        user = team_with_users.members.first()
        assert UserNotification.objects.filter(user=user).count() == 1
        user_notification = UserNotification.objects.get(user=user)
        assert user_notification.read is False, "UserNotification should be marked as unread"

        # Mark it as read to retrigger it when the same notification is created again
        user_notification.read = True
        user_notification.save()

        # Reset mocks
        mock_bust_cache.reset_mock()

        # Create another notification with same identifier
        create_notification(
            title="Test Notification", message="Test message", level=LevelChoices.ERROR, team=team_with_users
        )

        # Cache should be busted again (renotification)
        mock_bust_cache.assert_called()

        assert user.notifications.count() == 1
        assert UserNotification.objects.filter(user=user).count() == 1
        user_notification.refresh_from_db()
        assert user_notification.read is False, "UserNotification should be marked as unread again"

    def test_create_identifier(self):
        """
        Test identifier generation with and without event data.

        Verifies that:
        1. Non-empty event_data generates a non-empty identifier based on that event data
        2. No event data results in an empty identifier
        3. Different event_data produces different identifiers
        """

        # Create notification with event_data
        assert len(create_identifier(None)) > 0
        assert create_identifier({"action": "test", "id": 123}) != create_identifier({"action": "test", "id": 124})

    def test_empty_event_data_uses_notification_message_as_identifier(self, team_with_users):
        """
        Test that when event_data is None, the notification message is used to create the identifier.
        """

        # Create notification with event_data
        create_notification(
            title="Test Notification 1",
            message="A very unique message",
            level=LevelChoices.INFO,
            team=team_with_users,
            event_data=None,
        )
        notification = Notification.objects.first()
        assert b64decode(notification.identifier) == b'{"message": "A very unique message"}'
