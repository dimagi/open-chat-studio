from datetime import UTC, datetime, timedelta

import pytest

from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.column_filters import TimestampFilter


def _timestamp_filter() -> TimestampFilter:
    return TimestampFilter(label="Created On", column="created_at", query_param="created_on")


def _sessions_with_created_at(*offsets: timedelta) -> tuple[ExperimentSession, ...]:
    now = datetime.now(UTC)
    sessions = tuple(ExperimentSessionFactory.create() for _ in offsets)
    for session, offset in zip(sessions, offsets, strict=True):
        ExperimentSession.objects.filter(pk=session.pk).update(created_at=now + offset)
    return sessions


def _queryset_for(*sessions: ExperimentSession):
    return ExperimentSession.objects.filter(pk__in=[s.pk for s in sessions])


@pytest.mark.django_db()
def test_apply_range_accepts_a_named_timezone():
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_range(queryset, "1h", timezone="America/New_York")

    assert list(filtered) == [recent]


@pytest.mark.django_db()
def test_apply_range_accepts_a_recently_added_iana_zone():
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_range(queryset, "1h", timezone="America/Coyhaique")

    assert list(filtered) == [recent]


@pytest.mark.django_db()
def test_apply_range_accepts_a_missing_timezone():
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_range(queryset, "1h", timezone=None)

    assert list(filtered) == [recent]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "bad_timezone",
    [
        pytest.param("Mars/Phobos", id="unknown_zone_name"),
        pytest.param("/etc/passwd", id="absolute_path_key"),
        pytest.param("../../etc/passwd", id="path_traversal_key"),
        pytest.param(123, id="non_string_value"),
    ],
)
def test_apply_range_returns_queryset_unfiltered_for_invalid_timezone(bad_timezone):
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_range(queryset, "1h", timezone=bad_timezone)

    assert set(filtered) == {recent, old}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param("1w", id="unsupported_unit_suffix"),
        pytest.param("oneh", id="non_numeric_prefix"),
    ],
)
def test_apply_range_returns_queryset_unfiltered_for_malformed_value(bad_value):
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_range(queryset, bad_value, timezone="UTC")

    assert set(filtered) == {recent, old}


@pytest.mark.django_db()
def test_apply_after_filters_on_valid_iso_timestamp():
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)
    threshold = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    filtered = _timestamp_filter().apply_after(queryset, threshold)

    assert list(filtered) == [recent]


@pytest.mark.django_db()
def test_apply_after_returns_queryset_unfiltered_for_non_iso_value():
    recent, old = _sessions_with_created_at(timedelta(minutes=-5), timedelta(days=-10))
    queryset = _queryset_for(recent, old)

    filtered = _timestamp_filter().apply_after(queryset, "not-a-date")

    assert set(filtered) == {recent, old}
