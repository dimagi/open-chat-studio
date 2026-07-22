"""Tests for the explicit ``start``/``end`` window, ``granularity`` bucketing, and the max-window
guard (issue #3851)."""

import datetime

import pytest
from django.urls import reverse

from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _add_messages(session, *, human=0, ai=0, when=None):
    kwargs = {"created_at": when} if when else {}
    for message_type, count in ((ChatMessageType.HUMAN, human), (ChatMessageType.AI, ai)):
        for _ in range(count):
            ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", **kwargs)


def _utc(year, month, day, hour=0, minute=0):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)


@pytest.mark.django_db()
def test_explicit_window_is_half_open():
    """``[start, end)``: a message exactly at ``end`` is excluded, one at ``start`` included."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=1, when=_utc(2026, 7, 1))  # at start → in
    _add_messages(session, human=1, when=_utc(2026, 7, 15))  # inside → in
    _add_messages(session, human=1, when=_utc(2026, 8, 1))  # at end → out

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": "2026-07-01", "end": "2026-08-01"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"]["messages"]["human"] == 2
    assert data["period"]["start"] == "2026-07-01T00:00:00Z"
    assert data["period"]["end"] == "2026-08-01T00:00:00Z"
    assert data["granularity"] == "total"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("params", "detail_contains"),
    [
        pytest.param(
            {"metric": "messages", "period": "current_month", "start": "2026-07-01", "end": "2026-08-01"},
            "not both",
            id="period-and-window",
        ),
        pytest.param(
            {"metric": "messages", "start": "2026-07-01"},
            "both 'start' and 'end'",
            id="start-without-end",
        ),
        pytest.param(
            {"metric": "messages", "end": "2026-08-01"},
            "both 'start' and 'end'",
            id="end-without-start",
        ),
        pytest.param(
            {"metric": "messages", "start": "2026-08-01", "end": "2026-07-01"},
            "must be after",
            id="end-before-start",
        ),
    ],
)
def test_window_validation_errors(params, detail_contains):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(reverse(USAGE_URL), params)

    assert response.status_code == 400
    assert detail_contains.lower() in str(response.json()).lower()


@pytest.mark.django_db()
def test_daily_granularity_returns_one_zero_filled_row_per_bucket():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    # Distinct timestamps within a bucket, so a GROUP BY that leaked the model's default ordering
    # (created_at) would split the bucket into one row per message and undercount.
    _add_messages(session, human=1, when=_utc(2026, 7, 1, 9))
    _add_messages(session, human=1, when=_utc(2026, 7, 1, 11))
    _add_messages(session, human=1, ai=1, when=_utc(2026, 7, 3, 10))  # 7/2 stays empty

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": "2026-07-01", "end": "2026-07-04", "granularity": "daily"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [row["bucket_start"] for row in results] == [
        "2026-07-01T00:00:00Z",
        "2026-07-02T00:00:00Z",
        "2026-07-03T00:00:00Z",
    ]
    assert [row["messages"] for row in results] == [
        {"human": 2, "ai": 0, "total": 2},
        {"human": 0, "ai": 0, "total": 0},  # zero-filled empty bucket
        {"human": 1, "ai": 1, "total": 2},
    ]


@pytest.mark.django_db()
def test_daily_bucket_boundary_under_non_utc_tz():
    """A message at 23:30 UTC on 1 July is 1 July in UTC but already 2 July in Auckland (UTC+12),
    so the timezone decides which daily bucket it falls in."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=1, when=_utc(2026, 7, 1, 23, 30))

    client = ApiTestClient(user, team)
    utc = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": "2026-07-01", "end": "2026-07-03", "granularity": "daily"},
    ).json()
    akl = client.get(
        reverse(USAGE_URL),
        {
            "metric": "messages",
            "start": "2026-07-01",
            "end": "2026-07-03",
            "granularity": "daily",
            "tz": "Pacific/Auckland",
        },
    ).json()

    # UTC: the message lands in the 1 July bucket.
    utc_hits = {row["bucket_start"]: row["messages"]["human"] for row in utc["results"]}
    assert utc_hits == {"2026-07-01T00:00:00Z": 1, "2026-07-02T00:00:00Z": 0}

    # Auckland: buckets are local midnights (serialized in UTC, like the 'period' envelope), so
    # 1 July 00:00 +12 == 30 Jun 12:00Z and 2 July 00:00 +12 == 1 Jul 12:00Z; the message lands in
    # the 2 July (local) bucket.
    akl_hits = {row["bucket_start"]: row["messages"]["human"] for row in akl["results"]}
    assert akl_hits == {"2026-06-30T12:00:00Z": 0, "2026-07-01T12:00:00Z": 1}


@pytest.mark.django_db()
def test_monthly_granularity_buckets_by_calendar_month():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=1, when=_utc(2026, 1, 15))
    _add_messages(session, human=2, when=_utc(2026, 3, 15))  # Feb stays empty

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": "2026-01-01", "end": "2026-04-01", "granularity": "monthly"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [(row["bucket_start"], row["messages"]["human"]) for row in results] == [
        ("2026-01-01T00:00:00Z", 1),
        ("2026-02-01T00:00:00Z", 0),
        ("2026-03-01T00:00:00Z", 2),
    ]


@pytest.mark.django_db()
def test_weekly_granularity_buckets_start_on_monday():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    # 2026-07-01 is a Wednesday; its Monday is 2026-06-29.
    _add_messages(session, human=1, when=_utc(2026, 7, 1))

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": "2026-07-01", "end": "2026-07-08", "granularity": "weekly"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [row["bucket_start"] for row in results] == ["2026-06-29T00:00:00Z", "2026-07-06T00:00:00Z"]
    assert results[0]["messages"]["human"] == 1


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("granularity", "start", "end", "rejected"),
    [
        pytest.param("daily", "2020-01-01", "2026-01-01", True, id="daily-multi-year-rejected"),
        pytest.param("daily", "2026-01-01", "2026-06-01", False, id="daily-months-ok"),
        pytest.param("weekly", "2020-01-01", "2026-01-01", False, id="weekly-multi-year-ok"),
        pytest.param("total", "2000-01-01", "2026-01-01", False, id="total-unbounded"),
    ],
)
def test_max_window_guard(granularity, start, end, rejected):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "start": start, "end": end, "granularity": granularity},
    )

    if rejected:
        assert response.status_code == 400
        assert "too large" in str(response.json()).lower()
    else:
        assert response.status_code == 200


@pytest.mark.django_db()
@pytest.mark.parametrize("metric", ["cost", "tokens"])
def test_cost_and_tokens_reject_non_total_granularity(metric):
    """cost/tokens are total-only for now; requesting them with a finer granularity is a clear 400
    rather than silently dropping the metric from bucketed rows."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(reverse(USAGE_URL), {"metric": metric, "granularity": "daily"})

    assert response.status_code == 400
    body = str(response.json()).lower()
    assert metric in body
    assert "granularity=total" in body


@pytest.mark.django_db()
def test_cost_and_tokens_allowed_with_explicit_window_at_total():
    """An explicit window at the default 'total' granularity is fine for cost/tokens."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["cost", "tokens"], "start": "2026-07-01", "end": "2026-08-01"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["cost"] == {"total": "0.00000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 0, "completion": 0, "total": 0}
