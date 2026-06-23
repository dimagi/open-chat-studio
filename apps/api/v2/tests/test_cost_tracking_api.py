"""Tests for the v2 cost-tracking API: /usage/ and /pricing/."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.teams.models import Flag
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _enable_flag_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()
    return flag


def _usage(team, *, cost, when, **kwargs):
    return UsageRecordFactory.create(team=team, cost=Decimal(str(cost)), at=when, **kwargs)


def _other_team_rule(team, other_team):
    PricingRule.objects.create(
        team=other_team,
        provider_type="openai",
        model_name="other-team-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price="0.00099",
    )


def _closed_rule(team, other_team):
    closed = PricingRule.objects.create(
        team=team,
        provider_type="openai",
        model_name="closed-test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price="0.00010",
    )
    closed.effective_to = _NOW
    closed.save()


# URL routing


def test_usage_url_reverses():
    assert reverse("api:v2:cost_tracking:usage") == "/api/v2/cost_tracking/usage/"


def test_pricing_url_reverses():
    assert reverse("api:v2:cost_tracking:pricing") == "/api/v2/cost_tracking/pricing/"


def test_usage_url_resolves():
    match = resolve("/api/v2/cost_tracking/usage/")
    assert match.url_name == "usage"


# Authentication and flag gating


@pytest.mark.django_db()
class TestAuthAndFlag:
    """401 for no auth; 404 when the flag is off (surface hidden)."""

    def test_unauthenticated_returns_401(self):
        client = APIClient()
        assert client.get(reverse("api:v2:cost_tracking:usage")).status_code == 401
        assert client.get(reverse("api:v2:cost_tracking:pricing")).status_code == 401

    @pytest.mark.parametrize("endpoint", ["usage", "pricing"])
    def test_flag_off_returns_404(self, endpoint):
        team = TeamWithUsersFactory.create()
        user = team.members.first()
        client = ApiTestClient(user, team)

        response = client.get(reverse(f"api:v2:cost_tracking:{endpoint}"))

        assert response.status_code == 404

    @pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
    def test_flag_on_returns_200(self, auth_method):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        user = team.members.first()
        client = ApiTestClient(user, team, auth_method=auth_method)

        response = client.get(reverse("api:v2:cost_tracking:usage"))

        assert response.status_code == 200


# Usage endpoint


@pytest.mark.django_db()
class TestUsageEndpoint:
    def _client_for(self, team):
        user = team.members.first()
        return ApiTestClient(user, team)

    def test_empty_team_returns_zero_totals(self):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)

        response = self._client_for(team).get(reverse("api:v2:cost_tracking:usage"))

        body = response.json()
        assert body["summary"]["total_cost"] == "0.00000000"
        assert body["summary"]["delta_pct"] is None
        assert body["top_bots"] == []

    def test_reports_team_cost_and_top_bots(self):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        exp = ExperimentFactory.create(team=team, name="bot-a")
        _usage(team, cost="1.50", when=_NOW - timedelta(days=1), experiment=exp)

        response = self._client_for(team).get(reverse("api:v2:cost_tracking:usage"))

        body = response.json()
        assert Decimal(body["summary"]["total_cost"]) == Decimal("1.50000000")
        assert len(body["top_bots"]) == 1
        assert body["top_bots"][0]["experiment_name"] == "bot-a"

    def test_team_scoped(self):
        team = TeamWithUsersFactory.create()
        other = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        _enable_flag_for(other)
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1))

        response = self._client_for(team).get(reverse("api:v2:cost_tracking:usage"))

        assert Decimal(response.json()["summary"]["total_cost"]) == Decimal(0)

    def test_custom_period_window(self):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=100))

        url = reverse("api:v2:cost_tracking:usage")
        params = urlencode(
            {
                "start": (_NOW - timedelta(days=120)).isoformat(),
                "end": (_NOW - timedelta(days=80)).isoformat(),
            }
        )
        response = self._client_for(team).get(f"{url}?{params}")

        assert Decimal(response.json()["summary"]["total_cost"]) == Decimal("2.00000000")

    def test_invalid_date_returns_400(self):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)

        url = reverse("api:v2:cost_tracking:usage")
        response = self._client_for(team).get(f"{url}?start=not-a-date")

        assert response.status_code == 400


# Pricing endpoint


@pytest.mark.django_db()
class TestPricingEndpoint:
    def _client_for(self, team):
        user = team.members.first()
        return ApiTestClient(user, team)

    def test_returns_global_and_team_rules(self):
        team = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        # Global rule from seed migration already exists; add a team-scoped override.
        PricingRule.objects.create(
            team=team,
            provider_type="openai",
            model_name="gpt-4o-mini",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00010",
        )

        response = self._client_for(team).get(reverse("api:v2:cost_tracking:pricing"))

        scopes = {
            (r["provider_type"], r["model_name"], r["service_kind"]): r["scope"] for r in response.json()["rules"]
        }
        # Team-scoped override is present.
        assert scopes[("openai", "gpt-4o-mini", "llm_input")] == "team"

    @pytest.mark.parametrize(
        ("setup_rule", "excluded_name"),
        [
            pytest.param(_other_team_rule, "other-team-model", id="other-team-rule"),
            pytest.param(_closed_rule, "closed-test-model", id="closed-rule"),
        ],
    )
    def test_pricing_endpoint_excludes(self, setup_rule, excluded_name):
        team = TeamWithUsersFactory.create()
        other = TeamWithUsersFactory.create()
        _enable_flag_for(team)
        setup_rule(team, other)

        response = self._client_for(team).get(reverse("api:v2:cost_tracking:pricing"))

        names = {r["model_name"] for r in response.json()["rules"]}
        assert excluded_name not in names
