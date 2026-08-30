from urllib.parse import parse_qs

import pytest
from django.test import RequestFactory

from apps.ocs_notifications.filters import SeverityLevelFilter, build_toggle_options


@pytest.fixture()
def level_filter():
    return SeverityLevelFilter()


def _request(query_string=""):
    return RequestFactory().get(f"/notifications/?{query_string}")


class TestBuildToggleOptions:
    """Unit tests for the query-string math behind the notification-page toggle buttons.

    See NotificationHome (apps/ocs_notifications/views.py) for how these options reach the
    template, and notification_toggle_filters.html for how they're rendered.
    """

    def test_no_active_filter_all_options_inactive(self, level_filter):
        options = build_toggle_options(level_filter, _request())

        assert all(opt["is_active"] is False for opt in options)
        assert {opt["label"] for opt in options} == {"Info", "Warning", "Error"}

    def test_selecting_an_inactive_option_adds_it_with_any_of(self, level_filter):
        options = build_toggle_options(level_filter, _request())
        warning_option = next(opt for opt in options if opt["label"] == "Warning")

        query = parse_qs(warning_option["query_string"])
        assert query["f_level"] == ["1"]
        assert query["op_level"] == ["any of"]

    def test_toggling_off_the_only_active_option_clears_the_filter(self, level_filter):
        request = _request("f_level=1&op_level=any+of")
        options = build_toggle_options(level_filter, request)
        warning_option = next(opt for opt in options if opt["label"] == "Warning")

        assert warning_option["is_active"] is True
        query = parse_qs(warning_option["query_string"])
        assert "f_level" not in query
        assert "op_level" not in query

    def test_selecting_a_second_option_combines_with_any_of(self, level_filter):
        """Clicking Error while Warning is already active should OR them together, not replace
        the existing selection."""
        request = _request("f_level=1&op_level=any+of")
        options = build_toggle_options(level_filter, request)
        error_option = next(opt for opt in options if opt["label"] == "Error")

        query = parse_qs(error_option["query_string"])
        assert query["op_level"] == ["any of"]
        # Values travel tilde-CSV-encoded (see serialize_csv_tilde_values); order is sorted
        # for a deterministic query string.
        assert query["f_level"] == ["1~2"]

    def test_unrelated_query_params_are_preserved(self, level_filter):
        request = _request("f_read=%5Btrue%5D&op_read=any+of&page=2")
        options = build_toggle_options(level_filter, request)
        info_option = next(opt for opt in options if opt["label"] == "Info")

        query = parse_qs(info_option["query_string"])
        assert query["f_read"] == ["[true]"]
        assert query["op_read"] == ["any of"]
        assert query["page"] == ["2"]
