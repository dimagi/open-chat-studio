"""Tests for the ``chatbot``/``platform`` filters and the resolve-to-FK-id path (issue #3852)."""

import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.api.v2.usage.services import UsageQuery, _message_queryset, _session_queryset, resolve_query_filters
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _add_messages(session, *, human=0, ai=0, when=None):
    kwargs = {"created_at": when} if when else {}
    for message_type, count in ((ChatMessageType.HUMAN, human), (ChatMessageType.AI, ai)):
        for _ in range(count):
            ChatMessage.objects.create(chat=session.chat, message_type=message_type, content="x", **kwargs)


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
def test_platform_evaluations_filter_is_rejected():
    """`evaluations` is not a real usage platform (its sessions are excluded), so filtering by it is 400."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team)

    response = client.get(reverse(USAGE_URL), {"metric": "messages", "platform": "evaluations"})

    assert response.status_code == 400


@pytest.mark.django_db()
def test_id_filters_avoid_joining_experiment_and_participant_tables():
    """chatbot/participant filters resolve to FK ids, so the message/session queries filter on the FK
    columns and never join the experiment/participant tables."""
    team = TeamWithUsersFactory.create()
    chatbot = ExperimentFactory.create(team=team)
    session = ExperimentSessionFactory.create(team=team, experiment=chatbot)
    window = {
        "metrics": {"messages"},
        "tz": ZoneInfo("UTC"),
        "start": datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
        "end": datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC),
    }

    chatbot_q = resolve_query_filters(UsageQuery(team=team, chatbot=chatbot.public_id, **window))
    msg_sql = str(_message_queryset(chatbot_q).query).lower()
    assert "experiments_experimentsession" in msg_sql  # still joins the session it needs
    assert '"experiments_experiment" on' not in msg_sql  # but not the experiment table
    assert '"experiments_experiment" on' not in str(_session_queryset(chatbot_q).query).lower()

    participant_q = resolve_query_filters(UsageQuery(team=team, participant=session.participant.public_id, **window))
    assert '"experiments_participant" on' not in str(_message_queryset(participant_q).query).lower()
    assert '"experiments_participant" on' not in str(_session_queryset(participant_q).query).lower()


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
