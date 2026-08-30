from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.urls import reverse
from django.utils import timezone
from time_machine import travel

from apps.ocs_notifications.models import EventUser, UserNotificationPreferences
from apps.utils.factories.notifications import EventUserFactory, NotificationEventFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory


def _create_notification(*, user, team):
    """Create an EventUser with a matching NotificationEvent, so it satisfies the
    `latest_event_created_at__isnull=False` filter and shows up in the table (and is
    therefore eligible to be picked up by "mark all read")."""
    event_user = EventUserFactory.create(user=user, team=team, read=False, read_at=None)
    NotificationEventFactory.create(team=team, event_type=event_user.event_type)
    return event_user


@pytest.mark.django_db()
class TestToggleNotificationReadView:
    """Tests for ToggleNotificationReadView"""

    @patch("apps.ocs_notifications.utils.bust_unread_notification_cache")
    def test_toggle_read_status_on_off_on_and_bust_cache(self, mock_bust_cache, client, team_with_users):
        """
        Test that read status is toggled from on -> off -> on and that this busts the cache.

        This test verifies:
        1. Initial read status is False
        2. After first POST, read status toggles to True and read_at is set
        3. Cache is busted after first toggle
        4. After second POST, read status toggles back to False and read_at is None
        5. Cache is busted after second toggle
        """
        # Setup
        user = team_with_users.members.first()
        user_notification = EventUserFactory.create(
            user=user,
            team=team_with_users,
            read=False,
            read_at=None,
        )
        notification_id = user_notification.id

        # Login user and set team in session
        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        # Step 1: Verify initial state is read=False
        user_notification.refresh_from_db()
        assert user_notification.read is False
        assert user_notification.read_at is None

        # Step 2: Toggle read status to True
        url = reverse("ocs_notifications:toggle_notification_read", args=[notification_id])
        response = client.post(url)

        # Verify response is successful
        assert response.status_code == 200

        # Verify read status changed to True and read_at is set
        user_notification.refresh_from_db()
        assert user_notification.read is True
        assert user_notification.read_at is not None

        # Verify cache was busted
        mock_bust_cache.assert_called()
        first_cache_call = mock_bust_cache.call_args_list[0]
        assert first_cache_call[0][0] == user.id
        assert first_cache_call[1]["team_slug"] == team_with_users.slug

        # Step 3: Toggle read status back to False
        mock_bust_cache.reset_mock()
        response = client.post(url)

        # Verify response is successful
        assert response.status_code == 200

        # Verify read status changed back to False and read_at is None
        user_notification.refresh_from_db()
        assert user_notification.read is False
        assert user_notification.read_at is None

        # Verify cache was busted again
        mock_bust_cache.assert_called()
        second_cache_call = mock_bust_cache.call_args_list[0]
        assert second_cache_call[0][0] == user.id
        assert second_cache_call[1]["team_slug"] == team_with_users.slug

    def test_toggle_read_status_for_notification_in_a_non_current_team(self, client, team_with_users):
        """A user browsing the cross-team notifications list can still toggle read status on a
        notification that belongs to a team other than their current session team."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        user_notification = EventUserFactory.create(user=user, team=other_team, read=False, read_at=None)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id  # current team is NOT the notification's team
        session.save()

        url = reverse("ocs_notifications:toggle_notification_read", args=[user_notification.id])
        response = client.post(url)

        assert response.status_code == 200
        user_notification.refresh_from_db()
        assert user_notification.read is True

    def test_cannot_toggle_read_status_for_notification_in_a_team_user_is_not_a_member_of(
        self, client, team_with_users
    ):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        user_notification = EventUserFactory.create(user=user, team=other_team, read=False, read_at=None)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        url = reverse("ocs_notifications:toggle_notification_read", args=[user_notification.id])
        response = client.post(url)

        assert response.status_code == 404
        user_notification.refresh_from_db()
        assert user_notification.read is False


@pytest.mark.django_db()
class TestNotificationPreferencesView:
    """Tests for notification_preferences view"""

    @patch("apps.users.views.bust_unread_notification_cache")
    def test_user_preference_updates_persisted_and_cache_busted(self, mock_bust_cache, client, team_with_users):
        """
        Test that user preference updates are persisted and that it busts the cache.

        This test verifies:
        1. Preferences can be created for a user
        2. Form data is correctly saved to the database
        3. Cache is busted after preferences are updated
        4. User is redirected to user profile after successful save
        """
        # Setup
        user = team_with_users.members.first()

        # Login user and set team in session
        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        # Step 1: Verify preferences don't exist initially
        assert UserNotificationPreferences.objects.filter(user=user, team=team_with_users).exists() is False

        # Step 2: POST form data to update preferences
        url = reverse("users:save_notification_preferences")
        form_data = {
            "in_app_enabled": True,
            "in_app_level": "1",  # Warning
            "email_enabled": True,
            "email_level": "2",  # Error
        }

        response = client.post(url, data=form_data)

        # Verify redirect to user profile
        assert response.status_code == 302
        assert response.url == reverse("users:user_profile")

        # Step 3: Verify preferences were created/updated
        preferences = UserNotificationPreferences.objects.get(user=user, team=team_with_users)
        assert preferences.in_app_enabled is True
        assert preferences.in_app_level == 1
        assert preferences.email_enabled is True
        assert preferences.email_level == 2

        # Step 4: Verify cache was busted
        mock_bust_cache.assert_called_once()
        cache_call = mock_bust_cache.call_args
        assert cache_call[0][0] == user.id
        assert cache_call[1]["team_slug"] == team_with_users.slug

        # Step 5: Update preferences again and verify they're persisted correctly
        mock_bust_cache.reset_mock()
        form_data = {
            "in_app_enabled": False,
            "in_app_level": "0",  # Info
            "email_enabled": False,
            "email_level": "1",  # Warning
        }

        response = client.post(url, data=form_data)
        assert response.status_code == 302

        # Verify updated preferences
        preferences.refresh_from_db()
        assert preferences.in_app_enabled is False
        assert preferences.in_app_level == 0
        assert preferences.email_enabled is False
        assert preferences.email_level == 1

        # Verify cache was busted again
        mock_bust_cache.assert_called_once()
        cache_call = mock_bust_cache.call_args
        assert cache_call[0][0] == user.id
        assert cache_call[1]["team_slug"] == team_with_users.slug


@pytest.mark.django_db()
class TestMarkAllNotificationsReadView:
    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
    def test_marks_all_unread_as_read_and_busts_cache(self, mock_bust_cache, client, team_with_users):
        user = team_with_users.members.first()
        other_user = team_with_users.members.last()
        _create_notification(user=user, team=team_with_users)
        _create_notification(user=user, team=team_with_users)
        EventUserFactory.create(user=user, team=team_with_users, read=True)
        other_user_notification = EventUserFactory.create(user=other_user, team=team_with_users, read=False)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()
        url = reverse("ocs_notifications:mark_all_notifications_read")
        response = client.post(url)

        assert response.status_code == 200

        assert EventUser.objects.filter(user=user, team=team_with_users, read=False).count() == 0
        assert EventUser.objects.filter(user=user, team=team_with_users, read=True).count() == 3
        other_user_notification.refresh_from_db()
        assert other_user_notification.read is False
        mock_bust_cache.assert_called_once_with(user_id=user.id, team_slug=team_with_users.slug)

    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
    def test_marks_unread_across_all_of_the_users_teams(self, mock_bust_cache, client, team_with_users):
        """Mark-all-read applies to the whole cross-team list, not just the current session team."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        current_team_notification = _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.post(reverse("ocs_notifications:mark_all_notifications_read"))
        assert response.status_code == 200

        current_team_notification.refresh_from_db()
        other_team_notification.refresh_from_db()
        assert current_team_notification.read is True
        assert other_team_notification.read is True

        busted_team_slugs = {call.kwargs["team_slug"] for call in mock_bust_cache.call_args_list}
        assert busted_team_slugs == {team_with_users.slug, other_team.slug}

    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
    def test_does_not_mark_notifications_for_teams_user_is_not_a_member_of(
        self, mock_bust_cache, client, team_with_users
    ):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.post(reverse("ocs_notifications:mark_all_notifications_read"))
        assert response.status_code == 200

        other_team_notification.refresh_from_db()
        assert other_team_notification.read is False

    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
    def test_only_marks_read_within_the_currently_active_team_filter(self, mock_bust_cache, client, team_with_users):
        """When the table is filtered to a single team, mark-all-read must stay in sync with
        it and only touch that team's notifications -- not the user's other teams."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        current_team_notification = _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        query = urlencode({"f_team": f"[{team_with_users.id}]", "op_team": "any of"})
        url = f"{reverse('ocs_notifications:mark_all_notifications_read')}?{query}"
        response = client.post(url)
        assert response.status_code == 200

        current_team_notification.refresh_from_db()
        other_team_notification.refresh_from_db()
        assert current_team_notification.read is True
        assert other_team_notification.read is False

        mock_bust_cache.assert_called_once_with(user_id=user.id, team_slug=team_with_users.slug)


@pytest.mark.django_db()
class TestUserNotificationTableView:
    """Tests for the cross-team notifications list (UserNotificationTableView)."""

    def test_defaults_to_notifications_from_all_of_the_users_teams(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        current_team_notification = _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.get(reverse("ocs_notifications:notifications_table"))

        assert response.status_code == 200
        row_ids = {obj.id for obj in response.context["table"].data}
        assert row_ids == {current_team_notification.id, other_team_notification.id}

    def test_excludes_notifications_from_teams_the_user_is_not_a_member_of(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.get(reverse("ocs_notifications:notifications_table"))

        row_ids = {obj.id for obj in response.context["table"].data}
        assert other_team_notification.id not in row_ids

    def test_team_filter_narrows_results_to_the_selected_team(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        current_team_notification = _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.get(
            reverse("ocs_notifications:notifications_table"),
            {"f_team": f"[{team_with_users.id}]", "op_team": "any of"},
        )

        assert response.status_code == 200
        row_ids = {obj.id for obj in response.context["table"].data}
        assert row_ids == {current_team_notification.id}
        assert other_team_notification.id not in row_ids

    def test_malformed_team_filter_value_leaves_the_default_all_teams_scope(self, client, team_with_users):
        """A `f_team` value that fails to parse to any team ID (e.g. tampered/garbage input)
        must not be silently treated as "no teams match" -- it should fall back to the
        default all-teams scope, same as if no team filter were applied at all."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        current_team_notification = _create_notification(user=user, team=team_with_users)
        other_team_notification = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.get(
            reverse("ocs_notifications:notifications_table"),
            {"f_team": '["not-a-valid-id"]', "op_team": "any of"},
        )

        assert response.status_code == 200
        row_ids = {obj.id for obj in response.context["table"].data}
        assert row_ids == {current_team_notification.id, other_team_notification.id}


@pytest.mark.django_db()
class TestNotificationEventHome:
    def test_user_can_view_an_event_belonging_to_a_non_current_team(self, client, team_with_users):
        """Regression test: clicking a cross-team row must not 404 just because the event's
        team differs from the user's current session team."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        event_user = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id  # current team is NOT the event's team
        session.save()

        url = reverse("ocs_notifications:notification_event_home", args=[event_user.event_type_id])
        response = client.get(url)

        assert response.status_code == 200
        event_user.refresh_from_db()
        assert event_user.read is True

    def test_404s_for_an_event_belonging_to_a_team_the_user_is_not_a_member_of(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        event_user = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        url = reverse("ocs_notifications:notification_event_home", args=[event_user.event_type_id])
        response = client.get(url)

        assert response.status_code == 404

    def test_shows_the_events_team_in_context(self, client, team_with_users):
        """The detail page is reachable from a cross-team list, so it needs its own team
        indicator (see NotificationHome, which spans every team the user belongs to)."""
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        event_user = _create_notification(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        url = reverse("ocs_notifications:notification_event_home", args=[event_user.event_type_id])
        response = client.get(url)

        assert response.status_code == 200
        assert response.context["team"] == other_team
        assert other_team.name.encode() in response.content


@pytest.mark.django_db()
class TestMuteNotificationView:
    def test_can_mute_a_notification_belonging_to_a_non_current_team(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        event_user = EventUserFactory.create(user=user, team=other_team)

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        url = reverse("ocs_notifications:mute_notification", args=[event_user.id])
        response = client.post(url, data={"duration": "8h"})

        assert response.status_code == 200
        event_user.refresh_from_db()
        assert event_user.muted_until is not None


@pytest.mark.django_db()
class TestNotificationHome:
    def test_renders_the_do_not_disturb_widget_with_teams_and_silenced_preferences(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        UserNotificationPreferences.objects.create(
            user=user, team=team_with_users, do_not_disturb_until=timezone.now() + timezone.timedelta(hours=8)
        )

        client.force_login(user)
        session = client.session
        session["team"] = team_with_users.id
        session.save()

        response = client.get(reverse("ocs_notifications:notifications_home"))

        assert response.status_code == 200
        dnd_action = next(
            a for a in response.context["actions"] if a.url_name == "ocs_notifications:set_do_not_disturb"
        )
        assert {t.id for t in dnd_action.extra_context["teams"]} == {team_with_users.id, other_team.id}
        assert [p.team_id for p in dnd_action.extra_context["silenced_preferences"]] == [team_with_users.id]


@pytest.mark.django_db()
class TestSetDoNotDisturbView:
    def test_silences_only_the_selected_teams(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        client.force_login(user)
        with travel("2025-01-01 10:00:00+00:00", tick=False):
            url = reverse("ocs_notifications:set_do_not_disturb")
            response = client.post(url, data={"teams": [team_with_users.id], "duration": "8h"})

            assert response.status_code == 200
            pref = UserNotificationPreferences.objects.get(user=user, team=team_with_users)
            assert pref.do_not_disturb_until is not None
            assert pref.do_not_disturb_until - timezone.now() == timezone.timedelta(hours=8)
            assert not UserNotificationPreferences.objects.filter(user=user, team=other_team).exists()

    def test_all_teams_flag_silences_every_team_the_user_belongs_to(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)

        client.force_login(user)
        url = reverse("ocs_notifications:set_do_not_disturb")
        response = client.post(url, data={"all_teams": "on", "duration": "1d"})

        assert response.status_code == 200
        assert UserNotificationPreferences.objects.get(user=user, team=team_with_users).do_not_disturb_until
        assert UserNotificationPreferences.objects.get(user=user, team=other_team).do_not_disturb_until

    def test_cannot_silence_a_team_the_user_is_not_a_member_of(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()

        client.force_login(user)
        url = reverse("ocs_notifications:set_do_not_disturb")
        response = client.post(url, data={"teams": [other_team.id], "duration": "8h"})

        assert response.status_code == 200
        assert not UserNotificationPreferences.objects.filter(user=user, team=other_team).exists()

    def test_invalid_duration_makes_no_changes(self, client, team_with_users):
        user = team_with_users.members.first()
        client.force_login(user)

        url = reverse("ocs_notifications:set_do_not_disturb")
        response = client.post(url, data={"teams": [team_with_users.id], "duration": "not-a-real-duration"})

        assert response.status_code == 200
        assert not UserNotificationPreferences.objects.filter(user=user, team=team_with_users).exists()

    def test_no_teams_selected_makes_no_changes(self, client, team_with_users):
        user = team_with_users.members.first()
        client.force_login(user)

        url = reverse("ocs_notifications:set_do_not_disturb")
        response = client.post(url, data={"duration": "8h"})

        assert response.status_code == 200
        assert not UserNotificationPreferences.objects.filter(user=user).exists()

    def test_silencing_again_overwrites_the_existing_duration(self, client, team_with_users):
        user = team_with_users.members.first()
        UserNotificationPreferences.objects.create(
            user=user, team=team_with_users, do_not_disturb_until=timezone.now() + timezone.timedelta(hours=1)
        )
        client.force_login(user)

        with travel("2025-01-01 10:00:00+00:00", tick=False):
            url = reverse("ocs_notifications:set_do_not_disturb")
            response = client.post(url, data={"teams": [team_with_users.id], "duration": "1w"})

            assert response.status_code == 200
            pref = UserNotificationPreferences.objects.get(user=user, team=team_with_users)
            assert pref.do_not_disturb_until is not None
            assert pref.do_not_disturb_until - timezone.now() == timezone.timedelta(weeks=1)


@pytest.mark.django_db()
class TestCancelDoNotDisturbView:
    def test_cancels_do_not_disturb_for_the_given_team(self, client, team_with_users):
        user = team_with_users.members.first()
        UserNotificationPreferences.objects.create(
            user=user, team=team_with_users, do_not_disturb_until=timezone.now() + timezone.timedelta(hours=8)
        )
        client.force_login(user)

        url = reverse("ocs_notifications:cancel_do_not_disturb", args=[team_with_users.id])
        response = client.post(url)

        assert response.status_code == 200
        pref = UserNotificationPreferences.objects.get(user=user, team=team_with_users)
        assert pref.do_not_disturb_until is None

    def test_does_not_affect_do_not_disturb_on_other_teams(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        MembershipFactory.create(user=user, team=other_team)
        until = timezone.now() + timezone.timedelta(hours=8)
        UserNotificationPreferences.objects.create(user=user, team=team_with_users, do_not_disturb_until=until)
        UserNotificationPreferences.objects.create(user=user, team=other_team, do_not_disturb_until=until)
        client.force_login(user)

        url = reverse("ocs_notifications:cancel_do_not_disturb", args=[team_with_users.id])
        response = client.post(url)

        assert response.status_code == 200
        assert UserNotificationPreferences.objects.get(user=user, team=other_team).do_not_disturb_until == until

    def test_404s_for_a_team_the_user_is_not_a_member_of(self, client, team_with_users):
        user = team_with_users.members.first()
        other_team = TeamFactory.create()
        client.force_login(user)

        url = reverse("ocs_notifications:cancel_do_not_disturb", args=[other_team.id])
        response = client.post(url)

        assert response.status_code == 404
