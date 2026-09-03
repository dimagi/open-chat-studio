from unittest.mock import Mock, patch

import pytest
from django.db import transaction
from django.test import override_settings

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.slack import build_slack_message, send_slack_notification
from apps.ocs_notifications.tasks import send_slack_notification_async
from apps.ocs_notifications.tests.conftest import activate_flag_for_team
from apps.ocs_notifications.utils import create_notification, get_slack_notification_channels
from apps.teams.flags import Flags
from apps.utils.factories.notifications import NotificationChannelFactory, NotificationEventFactory
from apps.utils.factories.team import TeamFactory


def _create_event(team, level=LevelChoices.WARNING):
    return NotificationEventFactory.create(team=team, event_type__level=level)


@pytest.mark.django_db()
class TestGetSlackNotificationChannels:
    def test_no_channels_when_slack_disabled(self, team_with_users):
        NotificationChannelFactory.create(team=team_with_users, enabled=True, level=LevelChoices.INFO)
        with override_settings(SLACK_ENABLED=False):
            assert get_slack_notification_channels(team_with_users, LevelChoices.WARNING).count() == 0

    def test_flag_inactive_returns_none_even_when_enabled(self, team_with_users):
        NotificationChannelFactory.create(team=team_with_users, enabled=True, level=LevelChoices.INFO)
        with override_settings(SLACK_ENABLED=True):
            assert get_slack_notification_channels(team_with_users, LevelChoices.WARNING).count() == 0

    def test_ignores_disabled_channel_and_excludes_lower_level(self, team_with_users):
        activate_flag_for_team(Flags.SLACK_NOTIFICATIONS.slug, team_with_users)
        NotificationChannelFactory.create(team=team_with_users, enabled=False, level=LevelChoices.INFO)
        NotificationChannelFactory.create(team=team_with_users, enabled=True, level=LevelChoices.ERROR)
        matching = NotificationChannelFactory.create(team=team_with_users, enabled=True, level=LevelChoices.WARNING)

        with override_settings(SLACK_ENABLED=True):
            channels = get_slack_notification_channels(team_with_users, LevelChoices.WARNING)

        assert list(channels) == [matching]

    def test_scoped_to_team(self, team_with_users):
        other_team = TeamFactory()
        activate_flag_for_team(Flags.SLACK_NOTIFICATIONS.slug, team_with_users)
        activate_flag_for_team(Flags.SLACK_NOTIFICATIONS.slug, other_team)
        NotificationChannelFactory.create(team=other_team, enabled=True, level=LevelChoices.INFO)
        NotificationChannelFactory.create(team=team_with_users, enabled=True, level=LevelChoices.INFO)

        with override_settings(SLACK_ENABLED=True):
            assert get_slack_notification_channels(team_with_users, LevelChoices.ERROR).count() == 1


@pytest.mark.django_db()
class TestSendSlackNotification:
    def test_build_slack_message(self):
        notification_channel = NotificationChannelFactory.create()
        event = _create_event(notification_channel.team, LevelChoices.ERROR)

        message = build_slack_message(event)

        assert event.title in message
        assert event.message in message
        assert "Error" in message
        assert notification_channel.team.name in message
        assert "View in OCS" in message

    def test_send_slack_notification_posts_to_resolved_channel(self, team_with_users):
        notification_channel = NotificationChannelFactory.create(team=team_with_users, channel_name="#alerts")
        event = _create_event(team_with_users)

        service = Mock()
        service.get_channel_by_name.return_value = {"id": "C12345", "name": "alerts"}
        with patch.object(notification_channel.messaging_provider, "get_messaging_service", return_value=service):
            result = send_slack_notification(notification_channel, event)

        assert result is True
        service.send_text_message.assert_called_once()
        call_kwargs = service.send_text_message.call_args.kwargs
        assert call_kwargs["to"] == "C12345"
        assert call_kwargs["platform"].value == "slack"

    def test_send_slack_notification_falls_back_to_channel_name(self, team_with_users):
        notification_channel = NotificationChannelFactory.create(team=team_with_users, channel_name="#private")
        event = _create_event(team_with_users)

        service = Mock()
        service.get_channel_by_name.return_value = None
        with patch.object(notification_channel.messaging_provider, "get_messaging_service", return_value=service):
            result = send_slack_notification(notification_channel, event)

        assert result is True
        assert service.send_text_message.call_args.kwargs["to"] == "#private"

    def test_send_slack_notification_fails_gracefully(self, team_with_users):
        notification_channel = NotificationChannelFactory.create(team=team_with_users)
        event = _create_event(team_with_users)

        service = Mock()
        service.send_text_message.side_effect = RuntimeError("slack down")
        with patch.object(notification_channel.messaging_provider, "get_messaging_service", return_value=service):
            result = send_slack_notification(notification_channel, event)

        assert result is False

    @patch("apps.ocs_notifications.slack.send_slack_notification")
    def test_async_task_dispatches(self, mock_send, team_with_users):
        notification_channel = NotificationChannelFactory.create(team=team_with_users)
        event = _create_event(team_with_users)

        send_slack_notification_async(notification_channel.id, event.id)

        mock_send.assert_called_once()
        assert mock_send.call_args.args[0] == notification_channel
        assert mock_send.call_args.args[1].pk == event.pk

    @patch("apps.ocs_notifications.slack.send_slack_notification")
    def test_async_task_skips_when_channel_deleted(self, mock_send, team_with_users):
        channel = NotificationChannelFactory.create(team=team_with_users)
        event = _create_event(team_with_users)
        channel.delete()

        send_slack_notification_async(channel.id, event.id)

        mock_send.assert_not_called()

    @patch("apps.ocs_notifications.slack.send_slack_notification")
    def test_async_task_skips_when_event_deleted(self, mock_send, team_with_users):
        channel = NotificationChannelFactory.create(team=team_with_users)
        event = _create_event(team_with_users)
        event.delete()

        send_slack_notification_async(channel.id, event.id)

        mock_send.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestCreateNotificationSchedulesSlack:
    @patch("apps.ocs_notifications.utils.send_slack_notification_async.delay")
    @patch("apps.ocs_notifications.utils.get_slack_notification_channels")
    def test_schedules_delivery_for_matching_channel(self, mock_get_channels, mock_delay, team_with_users):
        channel = NotificationChannelFactory.create(team=team_with_users)
        mock_get_channels.return_value = [channel]

        create_notification(
            title="Test",
            message="Hello",
            level=LevelChoices.ERROR,
            team=team_with_users,
            slug="slack-schedule",
        )
        transaction.commit()

        assert mock_delay.called
        call_args, call_kwargs = mock_delay.call_args
        assert call_args == (channel.id,)
        assert "notification_event_id" in call_kwargs
