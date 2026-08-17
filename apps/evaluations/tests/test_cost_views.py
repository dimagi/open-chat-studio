"""Tests for the cost-tracking UI surfaces added to the evaluations views: the run list's
Cost column, the run detail page's cost breakdown, and the config page's aggregate summary.
All three are gated by the `flag_ai_cost_monitoring` Waffle flag, same as the dashboard panel
and the LLM provider page.
"""

from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from apps.cost_tracking.services.reporting import evaluation_run_costs
from apps.evaluations.models import EvaluationRunStatus
from apps.teams.models import Flag
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory, EvaluatorFactory


def _enable_flag_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()


@pytest.fixture()
def config(team_with_users):
    return EvaluationConfigFactory.create(team=team_with_users)


@pytest.fixture()
def run(team_with_users, config):
    return EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)


@pytest.mark.django_db()
class TestRunListCostColumn:
    def test_cost_column_hidden_when_flag_off(self, client, team_with_users, config, run):
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])

        content = client.get(url).content.decode()

        assert "Cost" not in content

    def test_cost_column_shows_run_cost_when_flag_on(self, client, team_with_users, config, run):
        _enable_flag_for(team_with_users)
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("1.23"),
            extra={"evaluation_run_id": run.id},
        )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])

        content = client.get(url).content.decode()

        assert "Cost" in content
        assert "1.23" in content

    def test_run_with_no_usage_shows_placeholder_when_flag_on(self, client, team_with_users, config, run):
        _enable_flag_for(team_with_users)
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])

        content = client.get(url).content.decode()

        assert "—" in content

    def test_cost_lookup_is_scoped_to_the_current_page_not_the_whole_config(self, client, team_with_users, config):
        """Regression: cost must be stamped after pagination slices the queryset, not
        before — otherwise every run for the config is loaded and priced on every
        request instead of just the page being rendered (default page size is 25)."""
        _enable_flag_for(team_with_users)
        runs = EvaluationRunFactory.create_batch(30, team=team_with_users, config=config)
        for run in runs:
            UsageRecordFactory.create(
                team=team_with_users, evaluation_config=config, cost=Decimal("1"), extra={"evaluation_run_id": run.id}
            )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])

        with mock.patch(
            "apps.evaluations.views.evaluation_config_views.evaluation_run_costs", wraps=evaluation_run_costs
        ) as spy:
            response = client.get(url)

        assert response.status_code == 200
        requested_run_ids = spy.call_args.args[1]
        assert len(requested_run_ids) == 25


@pytest.mark.django_db()
class TestRunDetailCost:
    def test_cost_card_hidden_when_flag_off(self, client, team_with_users, config, run):
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)

        assert "cost_tracking_enabled" not in response.context or response.context["cost_tracking_enabled"] is False
        assert "run_cost" not in response.context

    def test_cost_card_shows_breakdown_when_flag_on(self, client, team_with_users, config, run):
        _enable_flag_for(team_with_users)
        evaluator = EvaluatorFactory.create(team=team_with_users)
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            model_name="gpt-4o-mini",
            cost=Decimal("2.50"),
            quantity=1000,
            extra={"evaluation_run_id": run.id, "evaluator_id": evaluator.id},
        )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)
        content = response.content.decode()

        assert response.context["cost_tracking_enabled"] is True
        assert response.context["run_cost"].total_cost == Decimal("2.50")
        assert evaluator.name in content
        assert "gpt-4o-mini" in content


@pytest.mark.django_db()
class TestConfigAggregateCost:
    def test_summary_hidden_when_flag_off(self, client, team_with_users, config):
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])

        response = client.get(url)

        assert response.context["cost_tracking_enabled"] is False
        assert "cost_summary" not in response.context

    def test_summary_shows_all_time_and_last_30_days_when_flag_on(self, client, team_with_users, config, run):
        _enable_flag_for(team_with_users)
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("4.20"),
            extra={"evaluation_run_id": run.id},
        )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])

        response = client.get(url)

        assert response.context["cost_tracking_enabled"] is True
        assert response.context["cost_summary"].all_time == Decimal("4.20")
        assert response.context["cost_summary"].last_30_days == Decimal("4.20")
