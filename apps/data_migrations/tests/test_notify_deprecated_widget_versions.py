from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from field_audit.models import AuditAction

from apps.channels.models import ChannelPlatform
from apps.channels.widget_versions import WidgetDeprecation
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

FAKE_DEPRECATIONS = [WidgetDeprecation(below_version="0.6.0", sunset_at=datetime(2026, 9, 1, tzinfo=UTC))]

NOTIFY_PATCH = (
    "apps.data_migrations.management.commands.notify_deprecated_widget_versions.deprecated_widget_version_notification"
)


def _widget_channel(version=None, **kwargs):
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok", "allowed_domains": ["example.com"]},
        **kwargs,
    )
    if version:
        type(channel).objects.filter(pk=channel.pk).update(
            widget_version=version,
            widget_version_updated_at=timezone.now(),
            audit_action=AuditAction.IGNORE,
        )
        channel.refresh_from_db()
    return channel


@pytest.mark.django_db()
class TestNotifyDeprecatedWidgetVersionsCommand:
    def test_no_deprecations_configured(self, capsys):
        with patch("apps.channels.widget_versions.DEPRECATIONS", []):
            call_command("notify_deprecated_widget_versions", force=True)
        assert "No widget deprecations" in capsys.readouterr().out

    @patch(NOTIFY_PATCH)
    def test_notifies_team_with_deprecated_version(self, mock_notify):
        channel = _widget_channel(version="0.5.0")
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", force=True)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["team"] == channel.team
        assert channel.experiment.name in kwargs["affected_chatbots"]
        assert kwargs["versions"] == {"0.5.0"}

    @patch(NOTIFY_PATCH)
    def test_skips_team_on_current_version(self, mock_notify):
        _widget_channel(version="0.8.0")
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", force=True)
        mock_notify.assert_not_called()

    @patch(NOTIFY_PATCH)
    def test_unknown_version_with_recent_sessions_is_notified(self, mock_notify):
        channel = _widget_channel(version=None)
        ExperimentSessionFactory(experiment=channel.experiment, experiment_channel=channel)
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", force=True)
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["versions"] == {"unknown"}

    @patch(NOTIFY_PATCH)
    def test_unknown_version_without_recent_sessions_is_skipped(self, mock_notify):
        _widget_channel(version=None)  # no sessions at all
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", force=True)
        mock_notify.assert_not_called()

    @patch(NOTIFY_PATCH)
    def test_unknown_version_with_old_sessions_is_skipped(self, mock_notify):
        channel = _widget_channel(version=None)
        session = ExperimentSessionFactory(experiment=channel.experiment, experiment_channel=channel)
        type(session).objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=120))
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", force=True)
        mock_notify.assert_not_called()

    @patch(NOTIFY_PATCH)
    def test_dry_run_does_not_notify(self, mock_notify, capsys):
        _widget_channel(version="0.5.0")
        # force past the run-once slug, which the deploy-time data migration marks applied
        with patch("apps.channels.widget_versions.DEPRECATIONS", FAKE_DEPRECATIONS):
            call_command("notify_deprecated_widget_versions", dry_run=True, force=True)
        mock_notify.assert_not_called()
        assert "Would notify" in capsys.readouterr().out
