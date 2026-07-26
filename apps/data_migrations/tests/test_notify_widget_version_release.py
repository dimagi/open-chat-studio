from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory

NOTIFY_PATCH = (
    "apps.data_migrations.management.commands.notify_widget_version_release.widget_version_release_notification"
)


def _widget_channel(**kwargs):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok", "allowed_domains": ["example.com"]},
        **kwargs,
    )


@pytest.mark.django_db()
class TestNotifyWidgetVersionReleaseCommand:
    @patch(NOTIFY_PATCH)
    def test_no_widget_channels(self, mock_notify, capsys):
        call_command("notify_widget_version_release", widget_version="0.10.0", force=True)
        mock_notify.assert_not_called()
        assert "No teams use the embedded chat widget" in capsys.readouterr().out

    @patch(NOTIFY_PATCH)
    def test_notifies_every_widget_team_regardless_of_version(self, mock_notify):
        channel = _widget_channel()
        call_command(
            "notify_widget_version_release",
            widget_version="0.10.0",
            notes="Adds dark mode.",
            force=True,
        )
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["team"] == channel.team
        assert kwargs["version"] == "0.10.0"
        assert kwargs["notes"] == "Adds dark mode."
        assert channel.experiment.name in kwargs["affected_chatbots"]

    @patch(NOTIFY_PATCH)
    def test_groups_chatbots_by_team(self, mock_notify):
        channel = _widget_channel()
        other = _widget_channel(team=channel.team)
        call_command("notify_widget_version_release", widget_version="0.10.0", force=True)
        mock_notify.assert_called_once()
        chatbots = mock_notify.call_args.kwargs["affected_chatbots"]
        assert channel.experiment.name in chatbots
        assert other.experiment.name in chatbots

    @patch(NOTIFY_PATCH)
    def test_ignores_non_widget_channels(self, mock_notify):
        ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM)
        call_command("notify_widget_version_release", widget_version="0.10.0", force=True)
        mock_notify.assert_not_called()

    @patch(NOTIFY_PATCH)
    def test_defaults_to_latest_version(self, mock_notify):
        _widget_channel()
        with patch("apps.channels.widget_versions.LATEST_VERSION", "9.9.9"):
            call_command("notify_widget_version_release", force=True)
        assert mock_notify.call_args.kwargs["version"] == "9.9.9"

    @patch(NOTIFY_PATCH)
    def test_dry_run_does_not_notify(self, mock_notify, capsys):
        _widget_channel()
        call_command("notify_widget_version_release", widget_version="0.10.0", dry_run=True, force=True)
        mock_notify.assert_not_called()
        assert "Would notify" in capsys.readouterr().out
