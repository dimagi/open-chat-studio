"""Tests for the flag-gated Cost Tracking panel on the main dashboard."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord
from apps.teams.models import Flag

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _enable_flag_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()


def _usage(team, *, cost, when, experiment=None, confidence=Confidence.EXACT, quantity=100):
    record = UsageRecord.objects.create(
        team=team,
        service_kind=ServiceKind.LLM_INPUT,
        provider_type="openai",
        model_name="gpt-4o-mini",
        quantity=quantity,
        unit_price=Decimal("0.00015"),
        cost=Decimal(str(cost)),
        confidence=confidence,
        experiment=experiment,
    )
    UsageRecord.objects.filter(pk=record.pk).update(timestamp=when)
    return record


@pytest.mark.django_db()
class TestCostTrackingPanel:
    """The panel renders only when `flag_ai_cost_monitoring` is on for the team."""

    def _get_dashboard(self, authenticated_client, team):
        return authenticated_client.get(reverse("dashboard:index", kwargs={"team_slug": team.slug}))

    def test_hidden_when_flag_off(self, authenticated_client, team):
        response = self._get_dashboard(authenticated_client, team)

        assert response.status_code == 200
        assert response.context["cost_tracking_enabled"] is False
        assert b'data-testid="cost-tracking-panel"' not in response.content

    def test_visible_when_flag_on(self, authenticated_client, team):
        _enable_flag_for(team)

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_tracking_enabled"] is True
        assert b'data-testid="cost-tracking-panel"' in response.content

    def test_shows_team_total_cost(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(team, cost="2.50", when=_NOW - timedelta(days=1))

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].total_cost == Decimal("2.50000000")
        assert b"2.5000" in response.content

    def test_lists_top_bots(self, authenticated_client, team, experiment):
        _enable_flag_for(team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=experiment)

        response = self._get_dashboard(authenticated_client, team)

        bots = response.context["cost_top_bots"]
        assert len(bots) == 1
        assert bots[0].experiment_name == experiment.name

    def test_empty_state_when_no_usage(self, authenticated_client, team):
        _enable_flag_for(team)

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].total_cost == Decimal(0)
        assert response.context["cost_top_bots"] == []
        assert b"No chatbot usage in this period." in response.content

    def test_other_team_data_isolated(self, authenticated_client, team, experiment_team):
        _enable_flag_for(team)
        _enable_flag_for(experiment_team)
        _usage(experiment_team, cost="999.00", when=_NOW - timedelta(days=1))

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].total_cost == Decimal(0)
