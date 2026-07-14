from datetime import datetime

import pytz

from apps.utils.time import pretty_date


def test_pretty_date_includes_time_by_default():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=pytz.UTC)
    assert pretty_date(date, "UTC") == "Tuesday, 16 June 2026 14:32:05 UTC"


def test_pretty_date_day_precision():
    date = datetime(2026, 6, 16, 14, 32, 5, tzinfo=pytz.UTC)
    assert pretty_date(date, "UTC", include_time=False) == "Tuesday, 16 June 2026"
