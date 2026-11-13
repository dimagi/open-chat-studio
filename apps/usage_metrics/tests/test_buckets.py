"""The bucket vocabulary: Python-side bucket starts and the DB's truncation name the same instants."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db.models import Count

from apps.chat.models import ChatMessage, ChatMessageType
from apps.usage_metrics.buckets import Granularity, bucket_key, bucket_start, bucket_starts, by_bucket
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

ZONES = [
    pytest.param(ZoneInfo("UTC"), id="UTC"),
    pytest.param(ZoneInfo("Africa/Johannesburg"), id="Africa/Johannesburg"),
    pytest.param(ZoneInfo("Pacific/Auckland"), id="Pacific/Auckland"),
]
GRANULARITIES = [pytest.param(granularity, id=granularity.value) for granularity in Granularity]

_START = datetime(2026, 5, 25, tzinfo=UTC)
_END = datetime(2026, 7, 8, tzinfo=UTC)
# Instants placed on and around day, week and month edges, so every granularity has a boundary case.
_INSTANTS = [
    datetime(2026, 5, 31, 23, 30, tzinfo=UTC),
    datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
    datetime(2026, 6, 1, 10, 15, tzinfo=UTC),
    datetime(2026, 6, 7, 22, 45, tzinfo=UTC),
    datetime(2026, 6, 8, 11, 0, tzinfo=UTC),
    datetime(2026, 6, 30, 23, 59, tzinfo=UTC),
    datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
]


@pytest.mark.parametrize(
    ("granularity", "expected"),
    [
        pytest.param(Granularity.HOURLY, datetime(2026, 7, 1, 13, 0, tzinfo=UTC), id="hour"),
        pytest.param(Granularity.DAILY, datetime(2026, 7, 1, tzinfo=UTC), id="day"),
        pytest.param(Granularity.WEEKLY, datetime(2026, 6, 29, tzinfo=UTC), id="week-starts-monday"),
        pytest.param(Granularity.MONTHLY, datetime(2026, 7, 1, tzinfo=UTC), id="month"),
    ],
)
def test_bucket_start(granularity, expected):
    assert bucket_start(datetime(2026, 7, 1, 13, 42, 7, tzinfo=UTC), granularity) == expected


def test_bucket_key_names_the_same_start_for_a_date_and_a_datetime():
    tz = ZoneInfo("Africa/Johannesburg")

    assert bucket_key(date(2026, 6, 10), tz) == datetime(2026, 6, 10, tzinfo=tz)
    assert bucket_key(datetime(2026, 6, 9, 22, 0, tzinfo=UTC), tz) == datetime(2026, 6, 10, tzinfo=tz)


def test_bucket_starts_covers_the_window_from_the_boundary_before_start():
    tz = ZoneInfo("UTC")

    starts = list(bucket_starts(datetime(2026, 7, 1, 9, tzinfo=tz), datetime(2026, 7, 3, tzinfo=tz), "daily", tz))

    assert starts == [datetime(2026, 7, 1, tzinfo=tz), datetime(2026, 7, 2, tzinfo=tz)]


def test_daily_starts_stay_at_midnight_across_a_dst_change():
    """Europe/London moves its clocks on 2026-03-29, so the day has 23 hours and the next local
    midnight is an hour earlier in UTC than the one before."""
    tz = ZoneInfo("Europe/London")

    starts = list(bucket_starts(datetime(2026, 3, 28, tzinfo=tz), datetime(2026, 3, 31, tzinfo=tz), "daily", tz))

    assert [start.astimezone(UTC) for start in starts] == [
        datetime(2026, 3, 28, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 0, tzinfo=UTC),
        datetime(2026, 3, 29, 23, tzinfo=UTC),
    ]


def test_hourly_starts_step_in_elapsed_time_across_a_dst_change():
    tz = ZoneInfo("Europe/London")

    starts = list(
        bucket_starts(datetime(2026, 3, 29, 0, tzinfo=tz), datetime(2026, 3, 29, 3, tzinfo=UTC), "hourly", tz)
    )

    assert [start.astimezone(UTC) for start in starts] == [datetime(2026, 3, 29, hour, tzinfo=UTC) for hour in range(3)]


@pytest.mark.django_db()
@pytest.mark.parametrize("tz", ZONES)
@pytest.mark.parametrize("granularity", GRANULARITIES)
def test_db_truncation_lands_each_row_on_the_python_bucket_start(granularity, tz):
    """Every instant counts under the start `bucket_start` computes for it, and that start is one
    `bucket_starts` enumerates, so a zero-filled series and the DB rows join on equal instants."""
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team)
    for when in _INSTANTS:
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="x", created_at=when)
    expected: dict[datetime, int] = {}
    for when in _INSTANTS:
        start = bucket_start(when.astimezone(tz), granularity)
        expected[start] = expected.get(start, 0) + 1

    counted = {
        bucket: row["n"]
        for bucket, row in by_bucket(ChatMessage.objects.filter(chat__team=team), granularity, tz, n=Count("id"))
    }

    assert counted == expected
    assert set(counted) <= set(bucket_starts(_START, _END, granularity, tz))


@pytest.mark.django_db()
def test_by_bucket_truncates_the_named_field_in_the_given_zone():
    """23:30 UTC on the 9th is already the 10th in UTC+2, so the zone decides the daily bucket."""
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team)
    ChatMessage.objects.create(
        chat=session.chat,
        message_type=ChatMessageType.HUMAN,
        content="x",
        created_at=datetime(2026, 6, 9, 23, 30, tzinfo=UTC),
    )
    tz = ZoneInfo("Africa/Johannesburg")

    buckets = [
        bucket for bucket, _row in by_bucket(ChatMessage.objects.filter(chat__team=team), "daily", tz, n=Count("id"))
    ]

    assert buckets == [datetime(2026, 6, 10, tzinfo=tz)]
    assert buckets[0].astimezone(UTC) == datetime(2026, 6, 9, 22, tzinfo=UTC)


@pytest.mark.django_db()
def test_by_bucket_groups_by_a_second_field_when_asked():
    team = TeamFactory.create()
    first, second = ExperimentSessionFactory.create(team=team), ExperimentSessionFactory.create(team=team)
    when = datetime(2026, 6, 10, 12, tzinfo=UTC)
    for session, count in ((first, 2), (second, 1)):
        for _ in range(count):
            ChatMessage.objects.create(
                chat=session.chat, message_type=ChatMessageType.HUMAN, content="x", created_at=when
            )

    rows = {
        (bucket, row["chat__experiment_session"]): row["n"]
        for bucket, row in by_bucket(
            ChatMessage.objects.filter(chat__team=team),
            "daily",
            ZoneInfo("UTC"),
            group_by="chat__experiment_session",
            n=Count("id"),
        )
    }

    day = datetime(2026, 6, 10, tzinfo=UTC)
    assert rows == {(day, first.id): 2, (day, second.id): 1}


def test_an_unknown_granularity_is_rejected():
    with pytest.raises(ValueError, match="fortnightly"):
        bucket_start(datetime(2026, 7, 1, tzinfo=UTC), "fortnightly")


def test_monthly_starts_land_on_the_first_whatever_the_months_length():
    """A month step lands on the first of the next month, whatever the month's length."""
    tz = ZoneInfo("UTC")

    starts = list(bucket_starts(datetime(2026, 1, 31, tzinfo=tz), datetime(2026, 4, 1, tzinfo=tz), "monthly", tz))

    assert starts == [datetime(2026, month, 1, tzinfo=tz) for month in (1, 2, 3)]
    assert starts[1] - starts[0] == timedelta(days=31)
