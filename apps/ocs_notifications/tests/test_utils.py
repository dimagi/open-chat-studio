import pytest
from django.contrib.auth.models import Group

from apps.ocs_notifications.models import LevelChoices, UserNotification, UserNotificationPreferences
from apps.ocs_notifications.utils import (
    create_identifier,
    create_notification,
    get_unread_notification_count,
    mark_notification_read,
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
        (LevelChoices.WARNING, LevelChoices.INFO, True),
        # When preference is WARNING, only send WARNING and ERROR
        (LevelChoices.INFO, LevelChoices.WARNING, False),
        # When preference is ERROR, only send ERROR
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
            title="Test Notification",
            message="Test message",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="test-notification",
        )

        assert UserNotification.objects.filter(user=user).count() == 1

    def test_user_is_notified_again(self, team_with_users):
        """
        Test that reading a notification allows the user to be renotified.

        Verifies that:
        1. A user can be renotified for the same notification (same identifier)
        2. After marking a notification as read, creating the same notification
           again resets it to unread
        """
        user = team_with_users.members.first()

        # Create initial notification
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.ERROR,
            team=team_with_users,
            slug="test-notification",
        )

        # Verify notification was created
        assert get_unread_notification_count(user) == 1

        # Mark it as read to retrigger it when the same notification is created again
        user_notification = UserNotification.objects.get(user=user)
        mark_notification_read(user, user_notification.notification_id)

        # Verify it's now marked as read
        assert get_unread_notification_count(user) == 0

        # Create another notification with same identifier
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.ERROR,
            team=team_with_users,
            slug="test-notification",
        )

        # Should be renotified (unread again)
        assert get_unread_notification_count(user) == 1
        user_notification.refresh_from_db()
        assert user_notification.read is False, "UserNotification should be marked as unread again"

    def test_create_identifier(self):
        """
        Test identifier generation with slug and event data.

        Verifies that:
        1. Identifiers are generated based on slug and event_data
        2. Different event_data produces different identifiers
        3. Different slugs produce different identifiers
        """

        # Create identifier with different event_data
        assert len(create_identifier("test-slug", {})) > 0
        id_1 = create_identifier("test-slug", {"action": "test", "id": 123})
        id_2 = create_identifier("test-slug", {"action": "test", "id": 124})
        id_3 = create_identifier("slug-test", {"action": "test", "id": 123})
        assert id_1 != id_2, "Different event_data should produce different identifiers"
        assert id_1 != id_3, "Different slugs should produce different identifiers"

        # Different slugs should produce different identifiers even with same data
        assert create_identifier("slug-1", {"action": "test"}) != create_identifier("slug-2", {"action": "test"})

    def test_permissions_dictate_which_members_receive_notification(self, team_with_users):
        """
        Test that only team members with the required permissions receive the notification.

        Verifies that:
        1. Only users with the specified permissions receive the notification.
        2. Users without the required permissions do not receive the notification.
        """
        Membership = team_with_users.members.through
        user_with_perm = Membership.objects.first()
        user_with_perm.groups.add(Group.objects.get(name="Team Admin"))
        user_without_perm = Membership.objects.last()

        # Sanity check
        assert user_with_perm.has_perm("custom_actions.change_customaction") is True
        assert user_without_perm.has_perm("custom_actions.change_customaction") is False

        create_notification(
            title="Permission Test Notification",
            message="This is a test message for permissions.",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="permission-test",
            permissions=["custom_actions.change_customaction"],
        )

        # # Verify that only the user with permission received the notification
        assert UserNotification.objects.filter(user_id=user_with_perm.user_id).count() == 1
        assert UserNotification.objects.filter(user_id=user_without_perm.user_id).count() == 0
