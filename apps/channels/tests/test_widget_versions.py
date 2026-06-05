from datetime import UTC, datetime
from unittest.mock import patch

from apps.channels.widget_versions import (
    LATEST_VERSION,
    WidgetDeprecation,
    clean_widget_version,
    get_deprecation,
    get_widget_update_status,
    is_outdated,
    widget_script_url,
)

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
