from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.utils import timezone
from field_audit.models import AuditAction, AuditEvent

from apps.channels.forms import WidgetParams
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.8.0", "0.8.0"),
        (None, None),
        ("", None),
        ("not-a-version", None),
        ("1." * 30, None),
    ],
)
def test_clean_widget_version(raw, expected):
    assert clean_widget_version(raw) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.5.0", DEPRECATION),  # older than the bound
        ("0.6.0", None),  # boundary is not deprecated
        ("0.8.0", None),  # newer than the bound
        (None, DEPRECATION),  # unknown → older than everything
        ("garbage", DEPRECATION),  # unparseable → treated as unknown
    ],
)
@patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION])
def test_get_deprecation(version, expected):
    assert get_deprecation(version) == expected


@pytest.mark.parametrize("version", [None, "0.0.1"])
def test_get_deprecation_no_deprecations_configured(version):
    # DEPRECATIONS is patched empty to mimic the pre-first-deprecation state
    with patch("apps.channels.widget_versions.DEPRECATIONS", []):
        assert get_deprecation(version) is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.7.0", True),  # older than LATEST
        (LATEST_VERSION, False),  # current
        (None, False),  # unknown → no badge (deprecation still applies via get_deprecation)
    ],
)
def test_is_outdated(version, expected):
    assert is_outdated(version) is expected


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


@pytest.mark.django_db()
def test_widget_params_context_includes_version_info(widget_channel):
    type(widget_channel).objects.filter(pk=widget_channel.pk).update(
        widget_version="0.1.0",
        widget_version_updated_at=timezone.now(),
        audit_action=AuditAction.IGNORE,
    )
    widget_channel.refresh_from_db()
    widget = WidgetParams(experiment=widget_channel.experiment, widget_token="tok", channel=widget_channel)
    context = widget.get_context("widget_token", "", {})
    assert context["widget"]["version"] == "0.1.0"
    assert context["widget"]["version_status"] is not None
