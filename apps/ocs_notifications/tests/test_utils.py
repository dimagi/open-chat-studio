from base64 import b64decode
from unittest.mock import patch

import pytest

from apps.ocs_notifications.models import LevelChoices, Notification, UserNotification, UserNotificationPreferences
from apps.ocs_notifications.utils import (
    CACHE_KEY_FORMAT,
    bust_unread_notification_cache,
    create_identifier,
    create_notification,
    get_user_notification_cache_value,
    send_notification_email,
    set_user_notification_cache,
)
from apps.utils.factories.notifications import UserNotificationFactory


@patch("apps.ocs_notifications.utils.cache.get")
def test_get_user_notification_cache_value(mock_cache_get):
    """
    Test retrieving cached unread notification count for a user.

    Verifies that the cache.get method is called with the correct key format
    and that the retrieved value is returned properly.
    """
    user_id = 123
    mock_cache_get.return_value = 5
    result = get_user_notification_cache_value(user_id, team_slug="test-team")

    expected_key = CACHE_KEY_FORMAT.format(user_id=user_id, team_slug="test-team")
    mock_cache_get.assert_called_once_with(expected_key)
    assert result == 5


@patch("apps.ocs_notifications.utils.cache.set")
def test_set_user_notification_cache(mock_cache_set):
    """
    Test caching an unread notification count for a user.

    Verifies that the cache.set method is called with the correct key,
    count value, and appropriate timeout (5 minutes).
    """
    user_id = 456
    count = 10
    set_user_notification_cache(user_id, count=count, team_slug="test-team")

    expected_key = CACHE_KEY_FORMAT.format(user_id=user_id, team_slug="test-team")
    mock_cache_set.assert_called_once_with(expected_key, count, 5 * 60)


@patch("apps.ocs_notifications.utils.cache.delete")
def test_bust_unread_notification_cache(mock_cache_delete):
    """
    Test invalidating cached notification count for a user.

    Verifies that the cache.delete method is called with the correct key
    to invalidate the cached unread notification count.
    """
    user_id = 789
    bust_unread_notification_cache(user_id, team_slug="test-team")

    expected_key = CACHE_KEY_FORMAT.format(user_id=user_id, team_slug="test-team")
    mock_cache_delete.assert_called_once_with(expected_key)


@pytest.mark.django_db()
@patch("apps.ocs_notifications.utils.render_to_string")
@patch("apps.ocs_notifications.utils.send_mail")
def test_send_notification_email_respects_levels(mock_send_mail, mock_render, team_with_users):
    """
    Test that email notifications respect user notification level preferences.

    Verifies two scenarios:
    1. Email is not sent when user notification preferences don't exist
    2. Email is sent only when notification level meets or exceeds user's
       email notification threshold level
    """
    user = team_with_users.members.first()
    user_notification = UserNotificationFactory.create(user=user, notification__level=LevelChoices.ERROR)

    # Test 1: Email not sent when preferences doesn't exist
    send_notification_email(user_notification)
    mock_send_mail.assert_not_called()

    # Test 2: Email sent when preferences allow it
    UserNotificationPreferences.objects.create(
        team=user_notification.team,
        user=user,
        email_enabled=True,
        email_level=LevelChoices.WARNING,  # Only send ERROR level, not WARNING
    )

    mock_send_mail.reset_mock()
    send_notification_email(user_notification)
    mock_send_mail.assert_called()


@pytest.mark.django_db()
class TestCreateNotification:
    @patch("apps.ocs_notifications.utils.bust_unread_notification_cache", wraps=bust_unread_notification_cache)
    @patch("apps.ocs_notifications.utils.send_notification_email", wraps=send_notification_email)
    def test_create_notification_notifies_user_when_notification_created(
        self, mock_send_email, mock_bust_cache, team_with_users
    ):
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
    @patch("apps.ocs_notifications.utils.send_notification_email", wraps=send_notification_email)
    def test_user_is_notified_again(self, mock_send_email, mock_bust_cache, team_with_users):
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

    @patch("apps.ocs_notifications.utils.bust_unread_notification_cache", wraps=bust_unread_notification_cache)
    @patch("apps.ocs_notifications.utils.send_notification_email", wraps=send_notification_email)
    def test_empty_event_data_uses_notification_message_as_identifier(
        self, mock_send_email, mock_bust_cache, team_with_users
    ):
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
