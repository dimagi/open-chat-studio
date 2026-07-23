"""Tests for ``group_by`` breakdowns, cursor pagination, the ``chatbot``/``platform`` filters, and the
combo guards (issue #3852)."""

import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.api.v2.usage.services import MAX_GROUPED_ROWS, UsageQuery, grouped_page_size_cap
from apps.chat.models import ChatMessage, ChatMessageType
from apps.cost_tracking.models import ServiceKind
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _add_messages(session, *, human=0, ai=0, when=None):
    kwargs = {"created_at": when} if when else {}
    for message_type, count in ((ChatMessageType.HUMAN, human), (ChatMessageType.AI, ai)):
        for _ in range(count):
            ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", **kwargs)


@pytest.mark.django_db()
def test_group_by_participant_one_row_each_with_both_handles():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    alice = ParticipantFactory.create(team=team, identifier="alice@example.com", platform="web")
    bob = ParticipantFactory.create(team=team, identifier="bob@example.com", platform="web")
    _add_messages(ExperimentSessionFactory.create(team=team, participant=alice, platform="web"), human=3, ai=2)
    _add_messages(ExperimentSessionFactory.create(team=team, participant=bob, platform="web"), human=1)
    # An idle participant (no messages) is excluded from the breakdown.
    ParticipantFactory.create(team=team, identifier="idle@example.com", platform="web")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "participant"})

    assert response.status_code == 200
    data = response.json()
    assert data["group_by"] == "participant"
    assert data["count"] == 2
    by_identifier = {row["participant"]["identifier"]: row for row in data["results"]}
    assert set(by_identifier) == {"alice@example.com", "bob@example.com"}
    assert by_identifier["alice@example.com"]["participant"]["public_id"] == str(alice.public_id)
    assert by_identifier["alice@example.com"]["messages"] == {"human": 3, "ai": 2, "total": 5}
    assert by_identifier["bob@example.com"]["messages"] == {"human": 1, "ai": 0, "total": 1}


@pytest.mark.django_db()
def test_group_by_chatbot_rows_carry_public_id_and_name():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    support = ExperimentFactory.create(team=team, name="Support")
    sales = ExperimentFactory.create(team=team, name="Sales")
    _add_messages(ExperimentSessionFactory.create(team=team, experiment=support), human=4)
    _add_messages(ExperimentSessionFactory.create(team=team, experiment=sales), human=2)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "chatbot"})

    assert response.status_code == 200
    data = response.json()
    by_name = {row["chatbot"]["name"]: row for row in data["results"]}
    assert set(by_name) == {"Support", "Sales"}
    assert by_name["Support"]["chatbot"]["public_id"] == str(support.public_id)
    assert by_name["Support"]["messages"]["human"] == 4


@pytest.mark.django_db()
def test_group_by_platform_rows_are_slugs():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _add_messages(ExperimentSessionFactory.create(team=team, platform="web"), human=5)
    _add_messages(ExperimentSessionFactory.create(team=team, platform="whatsapp"), human=3)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "platform"})

    assert response.status_code == 200
    data = response.json()
    by_platform = {row["platform"]: row["messages"]["human"] for row in data["results"]}
    assert by_platform == {"web": 5, "whatsapp": 3}


@pytest.mark.django_db()
def test_grouped_results_are_cursor_paginated():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    for i in range(3):
        participant = ParticipantFactory.create(team=team, identifier=f"p{i}@example.com", platform="web")
        _add_messages(ExperimentSessionFactory.create(team=team, participant=participant, platform="web"), human=1)

    client = ApiTestClient(user, team)
    first = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "participant", "page_size": 2}).json()

    assert first["count"] == 3  # total only on the first page
    assert len(first["results"]) == 2
    assert first["next"] is not None

    second = client.get(first["next"]).json()
    assert len(second["results"]) == 1
    seen = {row["participant"]["identifier"] for row in first["results"] + second["results"]}
    assert seen == {"p0@example.com", "p1@example.com", "p2@example.com"}


@pytest.mark.django_db()
def test_platform_grouping_is_cursor_paginated():
    """Platform has no backing model, so it paginates a distinct-slug queryset ordered by the slug."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    for platform in ("web", "whatsapp", "telegram"):
        _add_messages(ExperimentSessionFactory.create(team=team, platform=platform), human=1)

    client = ApiTestClient(user, team)
    first = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "platform", "page_size": 2}).json()

    assert first["count"] == 3
    assert len(first["results"]) == 2
    assert first["next"] is not None
    second = client.get(first["next"]).json()
    seen = {row["platform"] for row in first["results"] + second["results"]}
    assert seen == {"web", "whatsapp", "telegram"}


@pytest.mark.django_db()
def test_chatbot_filter_narrows_all_metrics():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    target = ExperimentFactory.create(team=team)
    session = ExperimentSessionFactory.create(team=team, experiment=target)
    _add_messages(session, human=6)
    UsageRecordFactory.create(team=team, experiment=target, session=session, quantity=500, cost=Decimal("1.50"))
    # A different chatbot's activity that must be excluded.
    other_session = ExperimentSessionFactory.create(team=team)
    _add_messages(other_session, human=9)
    UsageRecordFactory.create(team=team, experiment=other_session.experiment, session=other_session, cost=Decimal("9"))

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["messages", "cost"], "chatbot": str(target.public_id)},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"]["messages"]["human"] == 6
    assert data["results"]["cost"]["total"] == "1.50000000"


@pytest.mark.django_db()
def test_platform_filter_narrows_all_metrics():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    web_session = ExperimentSessionFactory.create(team=team, platform="web")
    _add_messages(web_session, human=7)
    UsageRecordFactory.create(team=team, session=web_session, quantity=300, cost=Decimal("2"))
    wa_session = ExperimentSessionFactory.create(team=team, platform="whatsapp")
    _add_messages(wa_session, human=4)
    UsageRecordFactory.create(team=team, session=wa_session, cost=Decimal("5"))

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["messages", "cost"], "platform": "web"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"]["messages"]["human"] == 7
    assert data["results"]["cost"]["total"] == "2.00000000"


@pytest.mark.django_db()
def test_grouped_cost_and_tokens_per_group():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    alice = ParticipantFactory.create(team=team, identifier="alice@example.com", platform="web")
    session = ExperimentSessionFactory.create(team=team, participant=alice, platform="web")
    _add_messages(session, human=1)
    UsageRecordFactory.create(
        team=team,
        participant=alice,
        session=session,
        service_kind=ServiceKind.LLM_INPUT,
        quantity=1000,
        cost=Decimal("1"),
    )
    UsageRecordFactory.create(
        team=team,
        participant=alice,
        session=session,
        service_kind=ServiceKind.LLM_OUTPUT,
        quantity=400,
        cost=Decimal("2"),
    )

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["cost", "tokens"], "group_by": "participant"},
    )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["participant"]["identifier"] == "alice@example.com"
    assert row["cost"]["total"] == "3.00000000"
    assert row["tokens"] == {"prompt": 1000, "completion": 400, "total": 1400}


@pytest.mark.django_db()
def test_group_by_combined_with_granularity_gives_flat_bucket_rows():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    chatbot = ExperimentFactory.create(team=team, name="Daily")
    session = ExperimentSessionFactory.create(team=team, experiment=chatbot)
    _add_messages(session, human=1, when=datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.UTC))
    _add_messages(session, human=2, when=datetime.datetime(2026, 7, 2, 12, tzinfo=datetime.UTC))

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {
            "metric": "messages",
            "group_by": "chatbot",
            "granularity": "daily",
            "start": "2026-07-01",
            "end": "2026-07-03",
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    # one chatbot × two daily buckets = two flat rows, each carrying its bucket_start.
    assert len(results) == 2
    for row in results:
        assert row["chatbot"]["name"] == "Daily"
        assert "bucket_start" in row
    by_bucket = {row["bucket_start"][:10]: row["messages"]["human"] for row in results}
    assert by_bucket == {"2026-07-01": 1, "2026-07-02": 2}


@pytest.mark.django_db()
def test_grouped_pagination_stable_when_created_at_ties():
    """Participants created at the same instant must each appear exactly once when paged one at a time —
    the (-created_at, -pk) tiebreaker gives a stable total order so the boundary can't skip/duplicate."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    with time_machine.travel("2026-05-01T00:00:00+00:00"):  # all participants share a created_at
        participants = [
            ParticipantFactory.create(team=team, identifier=f"p{i}@example.com", platform="web") for i in range(5)
        ]
    for participant in participants:
        _add_messages(ExperimentSessionFactory.create(team=team, participant=participant, platform="web"), human=1)

    client = ApiTestClient(user, team)
    seen: list[str] = []
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "participant", "page_size": 1}).json()
    for _ in range(10):  # cap iterations so a pagination bug fails instead of looping forever
        seen.extend(row["participant"]["identifier"] for row in response["results"])
        if not response["next"]:
            break
        response = client.get(response["next"]).json()

    assert sorted(seen) == [f"p{i}@example.com" for i in range(5)]
    assert len(seen) == len(set(seen))  # each group exactly once, no boundary skip/duplicate


@pytest.mark.django_db()
def test_chatbot_filter_includes_archived_chatbot():
    """An archived chatbot's messages AND cost must both report — the message path reaches archived
    experiments by relation, so cost/tokens must resolve them too (not zero out asymmetrically)."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    chatbot = ExperimentFactory.create(team=team)
    session = ExperimentSessionFactory.create(team=team, experiment=chatbot)
    _add_messages(session, human=4)
    UsageRecordFactory.create(team=team, experiment=chatbot, session=session, cost=Decimal("7"))
    chatbot.is_archived = True
    chatbot.save(update_fields=["is_archived"])

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["messages", "cost"], "chatbot": str(chatbot.public_id)},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"]["messages"]["human"] == 4
    assert data["results"]["cost"]["total"] == "7.00000000"


@pytest.mark.django_db()
def test_group_by_chatbot_includes_archived_chatbot():
    """A chatbot archived after it was active in the window must still appear as a breakdown row, or the
    grouped rows silently fail to sum to the ungrouped totals (which count archived activity)."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    chatbot = ExperimentFactory.create(team=team, name="Archived")
    _add_messages(ExperimentSessionFactory.create(team=team, experiment=chatbot), human=3)
    chatbot.is_archived = True
    chatbot.save(update_fields=["is_archived"])

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "chatbot"})

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["results"][0]["chatbot"]["name"] == "Archived"
    assert data["results"][0]["messages"]["human"] == 3


def test_grouped_page_size_cap_shrinks_with_bucket_count():
    """The groups-per-page cap tightens as the bucket count grows, bounding groups × buckets rows."""
    base = {
        "team": None,
        "metrics": {"messages"},
        "tz": ZoneInfo("UTC"),
        "start": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        "end": datetime.datetime(2026, 4, 11, tzinfo=datetime.UTC),  # 100 days
    }
    assert grouped_page_size_cap(UsageQuery(**base, granularity="total")) == MAX_GROUPED_ROWS
    assert grouped_page_size_cap(UsageQuery(**base, granularity="daily")) == MAX_GROUPED_ROWS // 100


@pytest.mark.django_db()
def test_platform_evaluations_filter_is_rejected():
    """`evaluations` is not a real usage platform (its sessions are excluded), so filtering by it is 400."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(reverse(USAGE_URL), {"metric": "messages", "platform": "evaluations"})

    assert response.status_code == 400


@pytest.mark.django_db()
def test_participants_metric_grouped_by_chatbot_counts_distinct():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    chatbot = ExperimentFactory.create(team=team, name="Shared")
    alice = ParticipantFactory.create(team=team, identifier="alice@example.com", platform="web")
    bob = ParticipantFactory.create(team=team, identifier="bob@example.com", platform="web")
    # Two participants, two sessions each on the same chatbot → distinct count is 2, not 4.
    for participant in (alice, alice, bob):
        _add_messages(
            ExperimentSessionFactory.create(team=team, experiment=chatbot, participant=participant, platform="web"),
            human=1,
        )

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "participants", "group_by": "chatbot"})

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["chatbot"]["name"] == "Shared"
    assert row["participants"] == 2


@pytest.mark.django_db()
def test_participants_metric_with_group_by_participant_is_rejected():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(
        reverse(USAGE_URL),
        {"metric": "participants", "group_by": "participant"},
    )

    assert response.status_code == 400
    assert "redundant" in str(response.json()).lower()


@pytest.mark.django_db()
def test_tokens_only_grouped_skips_currency_scan():
    """A grouped tokens-only request must not run the extra DISTINCT-currency scan; a cost request does."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    alice = ParticipantFactory.create(team=team, identifier="alice@example.com", platform="web")
    session = ExperimentSessionFactory.create(team=team, participant=alice, platform="web")
    _add_messages(session, human=1)
    UsageRecordFactory.create(team=team, participant=alice, session=session, quantity=100, cost=Decimal("1"))
    client = ApiTestClient(user, team)

    def currency_scans(metric):
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse(USAGE_URL), {"metric": metric, "group_by": "participant"})
        assert response.status_code == 200
        return sum("distinct" in q["sql"].lower() and "currency" in q["sql"].lower() for q in ctx.captured_queries)

    assert currency_scans("tokens") == 0
    assert currency_scans("cost") >= 1


@pytest.mark.django_db()
def test_grouped_team_isolation():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    other_team = TeamWithUsersFactory.create()
    _add_messages(ExperimentSessionFactory.create(team=other_team), human=5)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "messages", "group_by": "participant"})

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["results"] == []


@pytest.mark.django_db()
def test_unknown_chatbot_filter_returns_zeroed_cost():
    """A chatbot filter matching no experiment must zero cost, not silently widen to the whole team."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    UsageRecordFactory.create(team=team, cost=Decimal("9"))

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": "cost", "chatbot": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 200
    assert response.json()["results"]["cost"] == {"total": "0.00000000", "currency": "USD"}
