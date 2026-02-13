import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone
from time_machine import travel

from apps.ocs_notifications.models import EventType, EventUser, LevelChoices, UserNotificationPreferences
from apps.ocs_notifications.utils import (
    DurationTimeDelta,
    create_identifier,
    create_notification,
    is_notification_muted,
    mute_notification,
    send_notification_email,
    toggle_notification_read,
)
from apps.teams.backends import add_user_to_team
from apps.utils.factories.notifications import EventTypeFactory, EventUserFactory, NotificationEventFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture(autouse=True)
def enable_flag_for_notifications():
    with patch("apps.ocs_notifications.utils._notifications_flag_is_active", return_value=True):
        yield


@pytest.mark.django_db()
def test_email_not_sent_when_preference_doesnt_exist(team_with_users, mailoutbox):
    """
    Test that email is not sent when user notification preferences don't exist.
    """
    user = team_with_users.members.first()
    event_type = EventTypeFactory.create(team=team_with_users, level=LevelChoices.ERROR)
    event_user = EventUserFactory.create(user=user, team=team_with_users, event_type=event_type)
    notification_event = NotificationEventFactory.create(team=team_with_users, event_type=event_type)

    send_notification_email(event_user, notification_event)
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
    event_type = EventTypeFactory.create(team=team_with_users, level=notification_level)
    event_user = EventUserFactory.create(user=user, team=team_with_users, event_type=event_type)
    notification_event = NotificationEventFactory.create(team=team_with_users, event_type=event_type)

    # Create user preferences with email level threshold
    UserNotificationPreferences.objects.create(
        team=event_user.team,
        user=user,
        email_enabled=True,
        email_level=email_preference_level,
    )

    send_notification_email(event_user, notification_event)

    if should_send:
        assert len(mailoutbox) == 1
    else:
        assert len(mailoutbox) == 0


@pytest.mark.django_db()
class TestCreateNotification:
    def test_creating_notification_stores_event_data(self, team_with_users):
        """
        Test that creating a notification stores the event data correctly.

        Verifies that:
        1. The event_data passed during notification creation is stored in the EventType model.
        """
        event_data = {"action": "test_event", "details": {"key": "value"}}

        create_notification(
            title="Event Data Test",
            message="Testing event data storage.",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="event-data-test",
            event_data=event_data,
        )

        identifier = create_identifier("event-data-test", event_data)
        event_type = EventType.objects.get(team=team_with_users, identifier=identifier)
        assert event_type.event_data == event_data, "Event data should be stored correctly in the EventType model."

    def test_create_notification_notifies_user_when_notification_created(self, team_with_users):
        """
        Test that creating a new notification notifies the recipient user.

        Verifies that:
        1. An EventUser entry is created for the specified user
        2. The user's unread notification cache is invalidated
        """
        user = team_with_users.members.first()
        assert EventUser.objects.filter(user=user).count() == 0

        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="test-notification",
        )

        assert EventUser.objects.filter(user=user).count() == 1

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
        assert user.unread_notifications_count(team_with_users) == 1

        # Mark it as read to retrigger it when the same notification is created again
        event_user = EventUser.objects.get(user=user)
        toggle_notification_read(user, event_user=event_user, read=True)

        # Verify it's now marked as read
        assert user.unread_notifications_count(team_with_users) == 0

        # Create another notification with same identifier
        create_notification(
            title="Test Notification",
            message="Test message",
            level=LevelChoices.ERROR,
            team=team_with_users,
            slug="test-notification",
        )

        # Should be renotified (unread again)
        assert user.unread_notifications_count(team_with_users) == 1
        event_user.refresh_from_db()
        assert event_user.read is False, "EventUser should be marked as unread again"

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
        assert EventUser.objects.filter(user_id=user_with_perm.user_id).count() == 1
        assert EventUser.objects.filter(user_id=user_without_perm.user_id).count() == 0

    @patch("apps.ocs_notifications.utils.create_identifier")
    def test_user_notification_not_created_when_muted(self, create_identifier, team_with_users):
        """
        Ensure that when a notification is muted for a user, creating a notification with the same identifier
        does not unmute or alter the existing EventUser for that user.
        """
        user = team_with_users.members.first()
        identifiers = ["muted-notification", "unmuted-notification"]
        create_identifier.side_effect = identifiers

        muted_event_type = EventType.objects.create(
            team=team_with_users,
            identifier="muted-notification",
            level=LevelChoices.INFO,
        )
        mute_notification(
            user=user,
            team=team_with_users,
            event_type=muted_event_type,
            timedelta=DurationTimeDelta.DURATION_8H,
        )

        create_notification(
            title="Muted Notification",
            message="This is not an important message.",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="slug",
            event_data={},
        )

        create_notification(
            title="Non-muted Notification",
            message="This is an important message.",
            level=LevelChoices.INFO,
            team=team_with_users,
            slug="slug",
            event_data={},
        )

        # Both notifications should have been created
        assert EventType.objects.filter(team=team_with_users, identifier__in=identifiers).count() == 2

        assert EventUser.objects.filter(user=user, event_type__identifier="muted-notification").count() == 1
        assert EventUser.objects.filter(user=user, event_type__identifier="unmuted-notification").count() == 1


@pytest.mark.django_db()
class TestNotificationMuting:
    @pytest.mark.parametrize("dnd_on", [True, False])
    def test_is_notification_muted_with_do_not_disturb(self, dnd_on, team_with_users):
        """
        Ensure that the is_notification_muted utility correctly identifies when a notification is muted based on the
        user's do not disturb preferences.
        """
        user = team_with_users.members.first()
        team2 = TeamFactory()
        add_user_to_team(
            team2, user
        )  # Add the same user to another team to ensure team-specific muting works as expected
        event_type = EventType.objects.create(
            team=team_with_users,
            identifier="dnd-test",
            level=LevelChoices.INFO,
        )
        event_type_team2 = EventType.objects.create(
            team=team2,
            identifier="dnd-test",
            level=LevelChoices.INFO,
        )

        UserNotificationPreferences.objects.create(
            team=team_with_users,
            user=user,
            do_not_disturb_until=timezone.now() + datetime.timedelta(hours=1) if dnd_on else None,
        )

        assert is_notification_muted(user, team_with_users, event_type) is dnd_on
        assert is_notification_muted(user, team2, event_type_team2) is False, (
            "Do Not Disturb should be team-specific and not affect other teams"
        )

    def test_mute_notification_for_8h(self, team_with_users):
        """Ensure that muting for 8 hours is team-specific and expires correctly."""
        user = team_with_users.members.first()
        team2 = TeamFactory()
        add_user_to_team(team2, user)
        event_type = EventType.objects.create(
            team=team_with_users,
            identifier="mute-8h-test",
            level=LevelChoices.INFO,
        )
        event_type_team2 = EventType.objects.create(
            team=team2,
            identifier="mute-8h-test",
            level=LevelChoices.INFO,
        )

        with travel(timezone.now(), tick=False) as freezer:
            mute_notification(
                user=user,
                team=team_with_users,
                event_type=event_type,
                timedelta=DurationTimeDelta.DURATION_8H,
            )

            # Verify team-specific muting
            assert is_notification_muted(user, team_with_users, event_type) is True
            assert is_notification_muted(user, team2, event_type_team2) is False, "Mute should not leak to other teams"

            # Verify expiration
            freezer.shift(datetime.timedelta(hours=8, minutes=1))
            assert is_notification_muted(user, team_with_users, event_type) is False

    def test_mute_notification_forever(self, team_with_users):
        """Ensure that muting indefinitely is team-specific and persists."""
        user = team_with_users.members.first()
        team2 = TeamFactory()
        add_user_to_team(team2, user)
        event_type = EventType.objects.create(
            team=team_with_users,
            identifier="mute-forever-test",
            level=LevelChoices.INFO,
        )
        event_type_team2 = EventType.objects.create(
            team=team2,
            identifier="mute-forever-test",
            level=LevelChoices.INFO,
        )

        with travel(timezone.now(), tick=False) as freezer:
            mute_notification(
                user=user,
                team=team_with_users,
                event_type=event_type,
                timedelta=DurationTimeDelta.FOREVER,
            )

            # Verify team-specific muting
            assert is_notification_muted(user, team_with_users, event_type) is True
            assert is_notification_muted(user, team2, event_type_team2) is False, "Mute should not leak to other teams"

            # Verify it stays muted long-term
            freezer.shift(datetime.timedelta(days=365 * 10))
            assert is_notification_muted(user, team_with_users, event_type) is True
