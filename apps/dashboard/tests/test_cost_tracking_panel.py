"""Tests for the flag-gated Cost Tracking panel on the main dashboard."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cost_tracking.models import Confidence
from apps.teams.models import Flag
from apps.utils.factories.cost_tracking import UsageRecordFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _enable_flag_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()


def _usage(team, *, cost, when, **kwargs):
    return UsageRecordFactory.create(team=team, cost=Decimal(str(cost)), at=when, **kwargs)


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
        assert b"2.50" in response.content

    def test_empty_state_when_no_usage(self, authenticated_client, team):
        _enable_flag_for(team)

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].total_cost == Decimal(0)
        assert b"No chatbot usage in this period." in response.content

    def test_hides_exact_estimated_split_without_estimated_spend(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=1), confidence=Confidence.EXACT)

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].estimated_cost == Decimal(0)
        assert b">Estimated<" not in response.content
        assert b">Exact<" not in response.content

    def test_shows_exact_estimated_split_with_estimated_spend(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), confidence=Confidence.EXACT)
        _usage(team, cost="0.50", when=_NOW - timedelta(days=1), confidence=Confidence.ESTIMATED)

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].estimated_cost > 0
        assert b">Estimated<" in response.content
        assert b">Exact<" in response.content

    def test_coverage_gap_detail_lists_models(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(
            team,
            cost="0.00",
            when=_NOW - timedelta(days=1),
            model_name="mystery-model",
            confidence=Confidence.EXACT,
        )

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].unpriced_call_count == 1
        assert b"mystery-model" in response.content

    def test_other_team_data_isolated(self, authenticated_client, team, experiment_team):
        _enable_flag_for(team)
        _enable_flag_for(experiment_team)
        _usage(experiment_team, cost="999.00", when=_NOW - timedelta(days=1))

        response = self._get_dashboard(authenticated_client, team)

        assert response.context["cost_summary"].total_cost == Decimal(0)


@pytest.mark.django_db()
class TestCostTrackingPanelEndpoint:
    """The dashboard JS refetches `api/cost-tracking-panel/` on filter change.
    Returns the rendered partial when the flag is on, empty body otherwise."""

    def _url(self, team):
        return reverse("dashboard:api_cost_tracking_panel", kwargs={"team_slug": team.slug})

    def test_empty_body_when_flag_off(self, authenticated_client, team):
        response = authenticated_client.get(self._url(team))

        assert response.status_code == 200
        assert response.content == b""

    def test_renders_panel_when_flag_on(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(team, cost="1.50", when=_NOW - timedelta(days=1))

        response = authenticated_client.get(self._url(team))

        assert response.status_code == 200
        assert b'data-testid="cost-tracking-panel"' in response.content
        assert b"1.50" in response.content

    def test_respects_filter_query_params(self, authenticated_client, team):
        _enable_flag_for(team)
        # Spend 100 days ago - inside a far-back filter window, outside default.
        _usage(team, cost="2.00", when=_NOW - timedelta(days=100))

        recent = authenticated_client.get(self._url(team) + "?date_range=7")
        far_back = authenticated_client.get(
            self._url(team) + "?date_range=custom&start_date=2026-02-01&end_date=2026-04-01"
        )

        # Default 7-day window misses the 100-days-ago row.
        assert b"No chatbot usage in this period" in recent.content
        # Custom window catches it.
        assert b"2.00" in far_back.content

    def test_team_isolated(self, authenticated_client, team, experiment_team):
        _enable_flag_for(team)
        _enable_flag_for(experiment_team)
        _usage(experiment_team, cost="999.00", when=_NOW - timedelta(days=1))

        response = authenticated_client.get(self._url(team))

        assert b"999" not in response.content


@pytest.mark.django_db()
class TestCostTimeseriesEndpoint:
    """`api/cost-timeseries/` feeds the panel's daily-spend chart. Flag-gated."""

    def _url(self, team):
        return reverse("dashboard:api_cost_timeseries", kwargs={"team_slug": team.slug})

    def test_empty_when_flag_off(self, authenticated_client, team):
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))

        response = authenticated_client.get(self._url(team))

        assert response.status_code == 200
        assert response.json() == []

    def test_returns_series_when_flag_on(self, authenticated_client, team):
        _enable_flag_for(team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))

        response = authenticated_client.get(
            self._url(team) + "?date_range=custom&start_date=2026-06-01&end_date=2026-06-20"
        )

        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["cost"] == 1.0


@pytest.mark.django_db()
class TestBotPerformanceCostColumns:
    """The Bot Performance API surfaces cost only when the team has the flag."""

    _RANGE = "?date_range=custom&start_date=2026-06-01&end_date=2026-06-20"

    def _url(self, team):
        return reverse("dashboard:api_bot_performance", kwargs={"team_slug": team.slug})

    def test_no_cost_fields_when_flag_off(self, authenticated_client, team, experiment):
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=experiment)

        response = authenticated_client.get(self._url(team) + self._RANGE)

        results = response.json()["results"]
        assert results
        assert "cost" not in results[0]

    def test_cost_fields_present_when_flag_on(self, authenticated_client, team, experiment):
        _enable_flag_for(team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=experiment)

        response = authenticated_client.get(self._url(team) + self._RANGE)

        row = next(r for r in response.json()["results"] if r["experiment_id"] == experiment.id)
        assert row["cost"] == 1.0
