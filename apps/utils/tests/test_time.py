from datetime import UTC, datetime

import pytest

from apps.utils.time import pretty_date


def test_pretty_date_includes_time_by_default():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=UTC)
    assert pretty_date(date, "UTC") == "Tuesday, 16 June 2026 14:32:05 UTC"


def test_pretty_date_day_precision():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=UTC)
    assert pretty_date(date, "UTC", include_time=False) == "Tuesday, 16 June 2026"


def test_pretty_date_renders_a_recently_added_zone():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=UTC)
    assert pretty_date(date, "America/Coyhaique") == "Tuesday, 16 June 2026 11:32:05 -03"


@pytest.mark.parametrize(
    "bad_timezone",
    [
        pytest.param("Mars/Phobos", id="unknown-but-well-formed-name"),
        pytest.param("../../x", id="malformed-key"),
        pytest.param(42, id="non-string-value"),
    ],
)
def test_pretty_date_falls_back_to_current_timezone_for_bad_input(bad_timezone):
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=UTC)
    assert pretty_date(date, bad_timezone) == pretty_date(date)


def test_pretty_date_converts_to_explicit_valid_zone():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=UTC)
    assert pretty_date(date, "Pacific/Auckland") == "Wednesday, 17 June 2026 02:32:05 NZST"
