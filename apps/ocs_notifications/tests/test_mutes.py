import pytest
from django.utils import timezone

from apps.ocs_notifications.models import LevelChoices, NotificationMute, UserNotification
from apps.ocs_notifications.utils import (
    create_notification,
    create_or_update_mute,
    delete_mute,
    is_notification_muted,
)
from apps.utils.factories.notifications import NotificationMuteFactory


@pytest.mark.django_db()
class TestNotificationMute:
    def test_create_permanent_mute_for_specific_type(self, team_with_users):
        """Test creating a permanent mute for a specific notification type"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        mute = create_or_update_mute(
            user=user, team=team_with_users, notification_type=notification_type, duration_hours=None
        )

        assert mute.notification_type == notification_type
        assert mute.muted_until is None
        assert mute.is_active() is True

    def test_create_temporary_mute_for_specific_type(self, team_with_users):
        """Test creating a temporary mute for a specific notification type"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        mute = create_or_update_mute(
            user=user, team=team_with_users, notification_type=notification_type, duration_hours=24
        )

        assert mute.notification_type == notification_type
        assert mute.muted_until is not None
        assert mute.is_active() is True

        # Check that muted_until is approximately 24 hours from now
        time_diff = mute.muted_until - timezone.now()
        assert 23.5 < time_diff.total_seconds() / 3600 < 24.5

    def test_create_permanent_mute_for_all_notifications(self, team_with_users):
        """Test creating a permanent mute for all notifications"""
        user = team_with_users.members.first()

        mute = create_or_update_mute(user=user, team=team_with_users, notification_type=None, duration_hours=None)

        assert mute.notification_type == ""
        assert mute.muted_until is None
        assert mute.is_active() is True

    def test_is_notification_muted_specific_type(self, team_with_users):
        """Test checking if a specific notification type is muted"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        # Before muting
        assert is_notification_muted(user, team_with_users, notification_type) is False

        # After muting
        create_or_update_mute(user=user, team=team_with_users, notification_type=notification_type, duration_hours=None)

        assert is_notification_muted(user, team_with_users, notification_type) is True
        assert is_notification_muted(user, team_with_users, "other-notification") is False

    def test_is_notification_muted_all_types(self, team_with_users):
        """Test checking if all notifications are muted"""
        user = team_with_users.members.first()

        # Mute all notifications
        create_or_update_mute(user=user, team=team_with_users, notification_type=None, duration_hours=None)

        # All notification types should be muted
        assert is_notification_muted(user, team_with_users, "any-notification") is True
        assert is_notification_muted(user, team_with_users, "another-notification") is True

    def test_muted_notification_not_created(self, team_with_users):
        """Test that muted notifications are not created for the user"""
        user = team_with_users.members.first()
        notification_slug = "test-notification"

        # Mute the notification type
        create_or_update_mute(user=user, team=team_with_users, notification_type=notification_slug, duration_hours=None)

        # Create a notification
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug=notification_slug,
        )

        # User should not have received the notification
        assert UserNotification.objects.filter(user=user).count() == 0

    def test_muted_all_notifications_not_created(self, team_with_users):
        """Test that when all notifications are muted, no notifications are created"""
        user = team_with_users.members.first()

        # Mute all notifications
        create_or_update_mute(user=user, team=team_with_users, notification_type=None, duration_hours=None)

        # Create a notification
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="any-notification",
        )

        # User should not have received the notification
        assert UserNotification.objects.filter(user=user).count() == 0

    def test_delete_mute(self, team_with_users):
        """Test deleting a notification mute"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        # Create a mute
        create_or_update_mute(user=user, team=team_with_users, notification_type=notification_type, duration_hours=None)

        assert is_notification_muted(user, team_with_users, notification_type) is True

        # Delete the mute
        delete_mute(user, team_with_users, notification_type)

        assert is_notification_muted(user, team_with_users, notification_type) is False

    def test_expired_mute_not_active(self, team_with_users):
        """Test that expired mutes are not active"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        # Create a mute that expired 1 hour ago
        mute = NotificationMuteFactory.create(
            user=user,
            team=team_with_users,
            notification_type=notification_type,
            muted_until=timezone.now() - timezone.timedelta(hours=1),
        )

        assert mute.is_active() is False
        assert is_notification_muted(user, team_with_users, notification_type) is False

    def test_update_existing_mute(self, team_with_users):
        """Test updating an existing mute changes the duration"""
        user = team_with_users.members.first()
        notification_type = "test-notification"

        # Create a permanent mute
        mute1 = create_or_update_mute(
            user=user, team=team_with_users, notification_type=notification_type, duration_hours=None
        )

        assert mute1.muted_until is None

        # Update to temporary mute
        mute2 = create_or_update_mute(
            user=user, team=team_with_users, notification_type=notification_type, duration_hours=24
        )

        # Should be the same object (updated)
        assert mute1.id == mute2.id
        assert mute2.muted_until is not None

        # Should only have one mute record
        assert (
            NotificationMute.objects.filter(
                user=user, team=team_with_users, notification_type=notification_type
            ).count()
            == 1
        )

    def test_different_users_have_separate_mutes(self, team_with_users):
        """Test that mutes are user-specific"""
        Membership = team_with_users.members.through
        user1 = Membership.objects.first().user
        user2 = Membership.objects.last().user
        notification_type = "test-notification"

        # Mute for user1 only
        create_or_update_mute(
            user=user1, team=team_with_users, notification_type=notification_type, duration_hours=None
        )

        # user1 should be muted
        assert is_notification_muted(user1, team_with_users, notification_type) is True

        # user2 should not be muted
        assert is_notification_muted(user2, team_with_users, notification_type) is False

        # Create notification
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug=notification_type,
        )

        # user1 should not receive notification
        assert UserNotification.objects.filter(user=user1).count() == 0

        # user2 should receive notification
        assert UserNotification.objects.filter(user=user2).count() == 1
