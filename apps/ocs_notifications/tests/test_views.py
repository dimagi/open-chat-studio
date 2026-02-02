from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.ocs_notifications.models import UserNotificationPreferences
from apps.utils.factories.notifications import UserNotificationFactory


@pytest.mark.django_db()
class TestToggleNotificationReadView:
    """Tests for ToggleNotificationReadView"""

    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
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
        user_notification = UserNotificationFactory.create(
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
        url = reverse("ocs_notifications:toggle_notification_read", args=[team_with_users.slug, notification_id])
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


@pytest.mark.django_db()
class TestNotificationPreferencesView:
    """Tests for notification_preferences view"""

    @patch("apps.ocs_notifications.views.bust_unread_notification_cache")
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
        url = reverse("ocs_notifications:notification_preferences")
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
        assert preferences.in_app_level == "1"
        assert preferences.email_enabled is True
        assert preferences.email_level == "2"

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
        assert preferences.in_app_level == "0"
        assert preferences.email_enabled is False
        assert preferences.email_level == "1"

        # Verify cache was busted again
        mock_bust_cache.assert_called_once()
        cache_call = mock_bust_cache.call_args
        assert cache_call[0][0] == user.id
        assert cache_call[1]["team_slug"] == team_with_users.slug
