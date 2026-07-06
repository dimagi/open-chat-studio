from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.utils import timezone
from field_audit.models import AuditAction, AuditEvent

from apps.channels.forms import WidgetParams
from apps.channels.models import ChannelPlatform
from apps.channels.widget_versions import (
    LATEST_VERSION,
    UNKNOWN_WIDGET_VERSION,
    WidgetDeprecation,
    clean_widget_version,
    get_deprecation,
    get_widget_update_status,
    is_deprecated,
    is_outdated,
    latest_deprecation,
    widget_script_url,
)
from apps.utils.factories.channels import ExperimentChannelFactory

DEPRECATION = WidgetDeprecation(below_version="0.6.0", sunset_at=datetime(2026, 9, 1, tzinfo=UTC))
NEWER_DEPRECATION = WidgetDeprecation(below_version="0.7.0", sunset_at=datetime(2026, 12, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("0.8.0", "0.8.0", id="valid-version"),
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("not-a-version", None, id="garbage"),
        pytest.param("1." * 30, None, id="too-long"),
    ],
)
def test_clean_widget_version(raw, expected):
    assert clean_widget_version(raw) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param("0.5.0", DEPRECATION, id="older-than-bound"),
        pytest.param("0.6.0", None, id="boundary-not-deprecated"),
        pytest.param("0.8.0", None, id="newer-than-bound"),
        pytest.param(None, DEPRECATION, id="unknown-is-older-than-everything"),
        pytest.param("garbage", DEPRECATION, id="unparseable-treated-as-unknown"),
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


@patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION, NEWER_DEPRECATION])
@pytest.mark.parametrize(
    "version",
    [
        pytest.param("0.5.0", id="below-both-bounds"),
        pytest.param("0.6.5", id="below-newer-bound-only"),
        pytest.param(None, id="unknown"),
    ],
)
def test_get_deprecation_returns_most_recent(version):
    # A version covered by several deprecations is reported under the highest-version one
    assert get_deprecation(version) == NEWER_DEPRECATION


@patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION, NEWER_DEPRECATION])
def test_latest_deprecation_returns_highest_below_version():
    assert latest_deprecation() == NEWER_DEPRECATION


def test_latest_deprecation_none_when_empty():
    with patch("apps.channels.widget_versions.DEPRECATIONS", []):
        assert latest_deprecation() is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param("0.5.0", True, id="older-than-bound"),
        pytest.param("0.6.0", False, id="boundary-not-deprecated"),
        pytest.param("0.8.0", False, id="newer-than-bound"),
        pytest.param(None, True, id="unknown-is-older-than-everything"),
        pytest.param("garbage", True, id="unparseable-treated-as-unknown"),
    ],
)
def test_is_deprecated(version, expected):
    assert is_deprecated(version, DEPRECATION) is expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param("0.7.0", True, id="older-than-latest"),
        pytest.param(LATEST_VERSION, False, id="current"),
        pytest.param(None, False, id="unknown-no-badge"),
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

    def test_deprecated_before_sunset_is_warning(self):
        future = WidgetDeprecation(below_version="0.6.0", sunset_at=timezone.now() + timedelta(days=30))
        with patch("apps.channels.widget_versions.DEPRECATIONS", [future]):
            status = get_widget_update_status("0.5.0")
        assert status.level == "warning"
        assert status.icon == "fa-triangle-exclamation"
        assert "deprecated" in status.message

    def test_past_sunset_is_error(self):
        expired = WidgetDeprecation(below_version="0.6.0", sunset_at=timezone.now() - timedelta(days=1))
        with patch("apps.channels.widget_versions.DEPRECATIONS", [expired]):
            status = get_widget_update_status("0.5.0")
        assert status.level == "error"
        assert status.icon == "fa-circle-xmark"
        assert "unsupported" in status.message


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

    def test_records_placeholder_when_no_header(self, widget_channel):
        # Pre-0.5.1 widgets send no header — record a placeholder, not nothing.
        widget_channel.record_widget_version(None)
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version == UNKNOWN_WIDGET_VERSION

    def test_placeholder_does_not_overwrite_real_version(self, widget_channel):
        widget_channel.record_widget_version("0.8.0")
        widget_channel.refresh_from_db()  # each request loads a fresh channel
        widget_channel.record_widget_version(None)
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version == "0.8.0"

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

    def test_widget_channel_with_placeholder_version(self, widget_channel):
        # The placeholder recorded for pre-header widgets must surface a badge.
        widget_channel.widget_version = UNKNOWN_WIDGET_VERSION
        with patch("apps.channels.widget_versions.DEPRECATIONS", [DEPRECATION]):
            status = widget_channel.widget_update_status
        assert status is not None
        assert status.deprecation is not None

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
