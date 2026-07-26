from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cost_tracking.models import ServiceKind
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

USAGE_URL = "api:v2:usage"


def _usage(team, *, kind=ServiceKind.LLM_INPUT, quantity=0, cost="0", **kwargs):
    """A current-month UsageRecord (default timestamp is now, so it lands in the current window)."""
    return UsageRecordFactory.create(team=team, service_kind=kind, quantity=quantity, cost=Decimal(cost), **kwargs)


@pytest.mark.django_db()
def test_cost_metric_returns_total_and_currency():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, cost="1.25", currency="USD")
    _usage(team, cost="0.75", currency="USD")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "cost"})

    assert response.status_code == 200
    assert response.json()["results"]["cost"] == {"total": "2.00000000", "currency": "USD"}


@pytest.mark.django_db()
def test_tokens_metric_splits_by_service_kind():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=1000)
    _usage(team, kind=ServiceKind.LLM_CACHED_INPUT, quantity=200)
    _usage(team, kind=ServiceKind.LLM_OUTPUT, quantity=400)
    _usage(team, kind=ServiceKind.LLM_CACHE_WRITE, quantity=50)

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": "tokens"})

    assert response.status_code == 200
    # prompt = input + cached input; completion = output; total = every LLM kind (incl. cache-write).
    assert response.json()["results"]["tokens"] == {"prompt": 1200, "completion": 400, "total": 1650}


@pytest.mark.django_db()
def test_cost_and_tokens_reconcile_against_usage_records():
    """cost and tokens for the same window are summed from the same rows, so both agree with a manual
    sum over ``UsageRecord``."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=300, cost="0.50")
    _usage(team, kind=ServiceKind.LLM_OUTPUT, quantity=100, cost="0.30")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["cost", "tokens"]})

    results = response.json()["results"]
    assert results["cost"] == {"total": "0.80000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 300, "completion": 100, "total": 400}


@pytest.mark.django_db()
def test_empty_window_zeroes_cost_and_tokens():
    team = TeamWithUsersFactory.create()
    user = team.members.first()

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["cost", "tokens"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["cost"] == {"total": "0.00000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 0, "completion": 0, "total": 0}


@pytest.mark.django_db()
def test_cost_tokens_compose_with_other_metrics():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=500, cost="1.00")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["messages", "cost", "tokens"]})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results["messages"] == {"human": 0, "ai": 0, "total": 0}
    assert results["cost"] == {"total": "1.00000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 500, "completion": 0, "total": 500}


@pytest.mark.django_db()
def test_participant_filter_applies_to_cost_and_tokens():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    target = ParticipantFactory.create(team=team)
    other = ParticipantFactory.create(team=team)
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=100, cost="1.00", participant=target)
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=999, cost="9.00", participant=other)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["cost", "tokens"], "participant": str(target.public_id)},
    )

    results = response.json()["results"]
    assert results["cost"] == {"total": "1.00000000", "currency": "USD"}
    assert results["tokens"]["total"] == 100


@pytest.mark.django_db()
def test_participant_filter_matching_no_one_zeroes_without_leaking():
    """A ``public_id`` that resolves to no participant must zero cost/tokens, not fall through to the
    team total — ``CostFilters`` treats an empty id list as "no filter", so the service short-circuits."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=100, cost="1.00")

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["cost", "tokens"], "participant": "00000000-0000-0000-0000-000000000000"},
    )

    results = response.json()["results"]
    assert results["cost"] == {"total": "0.00000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 0, "completion": 0, "total": 0}


@pytest.mark.django_db()
def test_participant_identifier_filter_spans_platforms():
    """An identifier can name several participants (one per platform); cost/tokens sum across them."""
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    web = ParticipantFactory.create(team=team, identifier="user@example.com", platform="web")
    api = ParticipantFactory.create(team=team, identifier="user@example.com", platform="api")
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=10, cost="0.10", participant=web)
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=20, cost="0.20", participant=api)

    client = ApiTestClient(user, team)
    response = client.get(
        reverse(USAGE_URL),
        {"metric": ["cost", "tokens"], "participant_identifier": "user@example.com"},
    )

    results = response.json()["results"]
    assert results["cost"] == {"total": "0.30000000", "currency": "USD"}
    assert results["tokens"]["total"] == 30


@pytest.mark.django_db()
def test_team_isolation_for_cost_and_tokens():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    other_team = TeamWithUsersFactory.create()
    _usage(other_team, kind=ServiceKind.LLM_INPUT, quantity=999, cost="9.99")

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["cost", "tokens"]})

    results = response.json()["results"]
    assert results["cost"] == {"total": "0.00000000", "currency": "USD"}
    assert results["tokens"] == {"prompt": 0, "completion": 0, "total": 0}


@pytest.mark.django_db()
def test_previous_month_window_excludes_current():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    _usage(team, kind=ServiceKind.LLM_INPUT, quantity=100, cost="1.00")  # current month

    client = ApiTestClient(user, team)
    response = client.get(reverse(USAGE_URL), {"metric": ["cost", "tokens"], "period": "previous_month"})

    results = response.json()["results"]
    assert results["cost"] == {"total": "0.00000000", "currency": "USD"}
    assert results["tokens"]["total"] == 0
