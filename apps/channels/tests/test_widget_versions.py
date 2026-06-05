from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.utils import timezone
from field_audit.models import AuditAction, AuditEvent

from apps.channels.models import ChannelPlatform
from apps.channels.widget_versions import (
    LATEST_VERSION,
    WidgetDeprecation,
    clean_widget_version,
    get_deprecation,
    get_widget_update_status,
    is_outdated,
    widget_script_url,
)
from apps.utils.factories.channels import ExperimentChannelFactory

DEPRECATION = WidgetDeprecation(below_version="0.6.0", sunset_at=datetime(2026, 9, 1, tzinfo=UTC))


class TestCleanWidgetVersion:
    def test_valid_version(self):
        assert clean_widget_version("0.8.0") == "0.8.0"

    def test_none(self):
        assert clean_widget_version(None) is None

    def test_empty(self):
        assert clean_widget_version("") is None

    def test_garbage(self):
        assert clean_widget_version("not-a-version") is None

    def test_too_long(self):
        assert clean_widget_version("1." * 30) is None


@patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION])
class TestGetDeprecation:
    def test_older_version_is_deprecated(self):
        assert get_deprecation("0.5.0") == DEPRECATION

    def test_boundary_version_is_not_deprecated(self):
        assert get_deprecation("0.6.0") is None

    def test_newer_version_is_not_deprecated(self):
        assert get_deprecation("0.8.0") is None

    def test_unknown_version_is_treated_as_older_than_everything(self):
        assert get_deprecation(None) == DEPRECATION

    def test_unparseable_version_is_treated_as_unknown(self):
        assert get_deprecation("garbage") == DEPRECATION


class TestGetDeprecationNoDeprecations:
    def test_no_deprecations_configured(self):
        # DEPRECATIONS is empty in the real module until the first deprecation lands
        with patch("apps.channels.widget_versions.DEPRECATIONS", []):
            assert get_deprecation(None) is None
            assert get_deprecation("0.0.1") is None


class TestIsOutdated:
    def test_older_is_outdated(self):
        assert is_outdated("0.7.0") is True

    def test_latest_is_not_outdated(self):
        assert is_outdated(LATEST_VERSION) is False

    def test_none_is_not_outdated(self):
        # Unknown version: no badge, but deprecation still applies via get_deprecation
        assert is_outdated(None) is False


class TestGetWidgetUpdateStatus:
    def test_no_version_reported(self):
        assert get_widget_update_status(None) is None

    def test_up_to_date(self):
        assert get_widget_update_status(LATEST_VERSION) is None

    def test_update_available(self):
        status = get_widget_update_status("0.7.0")
        assert status.level == "info"
        assert "0.7.0" in status.message
        assert LATEST_VERSION in status.message

    @patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION])
    def test_deprecated(self):
        status = get_widget_update_status("0.5.0")
        assert status.level == "warning"
        assert "deprecated" in status.message
        assert "2026" in status.message


def test_widget_script_url():
    assert widget_script_url() == (
        f"https://unpkg.com/open-chat-studio-widget@{LATEST_VERSION}"
        "/dist/open-chat-studio-widget/open-chat-studio-widget.esm.js"
    )


def test_widget_script_url_template_tag():
    tpl = Template("{% load chat_widget_tags %}{% widget_script_url %}")
    rendered = tpl.render(Context())
    assert f"open-chat-studio-widget@{LATEST_VERSION}" in rendered


@pytest.fixture()
def widget_channel(db):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok", "allowed_domains": ["example.com"]},
    )


@pytest.mark.django_db()
class TestRecordWidgetVersion:
    def test_records_new_version(self, widget_channel):
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version == "0.8.0"
        assert widget_channel.widget_version_updated_at is not None

    def test_ignores_garbage(self, widget_channel):
        widget_channel.record_widget_version("<script>")
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version is None

    def test_ignores_missing_header(self, widget_channel):
        widget_channel.record_widget_version(None)
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version is None

    def test_skips_write_when_fresh_and_unchanged(self, widget_channel):
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()
        first_seen = widget_channel.widget_version_updated_at
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version_updated_at == first_seen

    def test_writes_when_version_changes(self, widget_channel):
        widget_channel.record_widget_version("0.7.0")
        widget_channel.refresh_from_db()
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version == "0.8.0"

    def test_refreshes_stale_timestamp(self, widget_channel):
        stale = timezone.now() - timezone.timedelta(days=2)
        type(widget_channel).objects.filter(pk=widget_channel.pk).update(
            widget_version="0.8.0", widget_version_updated_at=stale, audit_action=AuditAction.IGNORE
        )
        widget_channel.refresh_from_db()
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version_updated_at > stale

    def test_does_not_create_audit_events(self, widget_channel):
        before = AuditEvent.objects.count()
        widget_channel.record_widget_version("0.8.0")
        assert AuditEvent.objects.count() == before


@pytest.mark.django_db()
class TestWidgetUpdateStatusProperty:
    def test_widget_channel_with_old_version(self, widget_channel):
        widget_channel.widget_version = "0.1.0"
        assert widget_channel.widget_update_status is not None

    def test_widget_channel_without_version(self, widget_channel):
        assert widget_channel.widget_update_status is None

    def test_non_widget_channel(self):
        channel = ExperimentChannelFactory()  # telegram
        assert channel.widget_update_status is None
