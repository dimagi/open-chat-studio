import datetime
import uuid
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from apps.api.v2.usage.param_serializers import UsageQuerySerializer
from apps.api.v2.usage.services import PERIOD_CURRENT_MONTH, PERIOD_PREVIOUS_MONTH, _month_bounds
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _add_messages(session, *, human=0, ai=0, system=0, when=None):
    """Create messages of each type on a session's chat, optionally backdated to ``when``."""
    kwargs = {"created_at": when} if when else {}
    for message_type, count in (
        (ChatMessageType.HUMAN, human),
        (ChatMessageType.AI, ai),
        (ChatMessageType.SYSTEM, system),
    ):
        for _ in range(count):
            ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", **kwargs)


def test_usage_url_reverses_and_resolves():
    assert reverse(USAGE_URL) == "/api/v2/usage/"
    assert resolve("/api/v2/usage/").url_name == "usage"


@pytest.mark.django_db()
def test_usage_unauthenticated():
    response = APIClient().get(reverse(USAGE_URL), {"metric": "messages"})
    assert response.status_code == 401


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_messages_metric_counts_current_month(auth_method):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=3, ai=2, system=1)

    client = ApiTestClient(user, team, auth_method=auth_method)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "period": "current_month"})

    assert response.status_code == 200
    data = response.json()
    # total excludes system messages: 3 human + 2 ai.
    assert data["results"]["messages"] == {"human": 3, "ai": 2, "total": 5}
    assert data["group_by"] is None
    assert data["period"]["timezone"] == "UTC"


@pytest.mark.django_db()
def test_filter_by_participant_public_id():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    target = ExperimentSessionFactory.create(team=team)
    other = ExperimentSessionFactory.create(team=team)
    _add_messages(target, human=4)
    _add_messages(other, human=9)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "participant": str(target.participant.public_id)},
    )

    assert response.status_code == 200
    assert response.json()["results"]["messages"]["human"] == 4


@pytest.mark.django_db()
def test_filter_by_participant_identifier():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    participant = ParticipantFactory.create(team=team, identifier="user@example.com")
    session = ExperimentSessionFactory.create(team=team, participant=participant)
    ExperimentSessionFactory.create(team=team)  # a different participant, uncounted
    _add_messages(session, human=2, ai=2)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "participant_identifier": "user@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["results"]["messages"] == {"human": 2, "ai": 2, "total": 4}


@pytest.mark.django_db()
def test_team_isolation():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    other_team = TeamWithUsersFactory.create()
    _add_messages(ExperimentSessionFactory.create(team=other_team), human=5)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages"})

    assert response.status_code == 200
    assert response.json()["results"]["messages"]["total"] == 0


@pytest.mark.django_db()
@time_machine.travel("2026-03-15T12:00:00+00:00")
def test_previous_month_excludes_current_month():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=3, when=datetime.datetime(2026, 3, 10, tzinfo=datetime.UTC))  # current
    _add_messages(session, human=7, when=datetime.datetime(2026, 2, 10, tzinfo=datetime.UTC))  # previous

    client = ApiTestClient(user, team)
    current = client.get(reverse(USAGE_URL), {"metric": "messages", "period": "current_month"}).json()
    previous = client.get(reverse(USAGE_URL), {"metric": "messages", "period": "previous_month"}).json()

    assert current["results"]["messages"]["human"] == 3
    assert previous["results"]["messages"]["human"] == 7


@pytest.mark.django_db()
@time_machine.travel("2026-04-15T12:00:00+00:00")
def test_tz_shifts_month_boundary():
    """ "Now" is mid-April in both zones, so ``current_month`` is unambiguously April. A message at
    23:30 UTC on 31 March is still March in UTC but already 1 April in Auckland (UTC+13), so the
    timezone alone decides whether it lands in April's window."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    session = ExperimentSessionFactory.create(team=team)
    _add_messages(session, human=1, when=datetime.datetime(2026, 3, 31, 23, 30, tzinfo=datetime.UTC))

    client = ApiTestClient(user, team)
    april_utc = client.get(reverse(USAGE_URL), {"metric": "messages", "period": "current_month"}).json()
    april_akl = client.get(
        reverse(USAGE_URL),
        {"metric": "messages", "period": "current_month", "tz": "Pacific/Auckland"},
    ).json()

    assert april_utc["results"]["messages"]["human"] == 0  # still March in UTC
    assert april_akl["results"]["messages"]["human"] == 1  # April in Auckland


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("params", "detail_contains"),
    [
        pytest.param({}, "metric", id="missing-metric"),
        pytest.param({"metric": "bogus"}, "Unknown value", id="unknown-metric"),
        pytest.param({"metric": "messages", "tz": "Not/AZone"}, "timezone", id="bad-timezone"),
        pytest.param(
            {
                "metric": "messages",
                "participant": "00000000-0000-0000-0000-000000000000",
                "participant_identifier": "x",
            },
            "only one of",
            id="both-participant-filters",
        ),
    ],
)
def test_invalid_params_return_400(params, detail_contains):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(reverse(USAGE_URL), params)

    assert response.status_code == 400
    assert detail_contains.lower() in str(response.json()).lower()


@pytest.mark.django_db()
def test_unknown_participant_returns_zeroed_block():
    """An unknown participant is an empty result, not a 404 (per the design doc)."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "participant": str(uuid.uuid4())})

    assert response.status_code == 200
    assert response.json()["results"]["messages"] == {"human": 0, "ai": 0, "total": 0}


def test_metric_list_is_deduplicated_and_order_preserved():
    serializer = UsageQuerySerializer(data={"metric": "messages, messages"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["metric"] == ["messages"]


@pytest.mark.parametrize(
    ("now", "period", "expected_start", "expected_end"),
    [
        pytest.param(
            "2026-01-15T12:00:00+00:00",
            PERIOD_PREVIOUS_MONTH,
            datetime.datetime(2025, 12, 1, tzinfo=ZoneInfo("UTC")),
            datetime.datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")),
            id="previous-month-crosses-year-boundary",
        ),
        pytest.param(
            "2026-12-15T12:00:00+00:00",
            PERIOD_CURRENT_MONTH,
            datetime.datetime(2026, 12, 1, tzinfo=ZoneInfo("UTC")),
            datetime.datetime(2027, 1, 1, tzinfo=ZoneInfo("UTC")),
            id="current-month-rolls-into-next-year",
        ),
    ],
)
def test_month_bounds_handles_year_rollover(now, period, expected_start, expected_end):
    with time_machine.travel(now):
        start, end = _month_bounds(period, ZoneInfo("UTC"))
    assert start == expected_start
    assert end == expected_end
