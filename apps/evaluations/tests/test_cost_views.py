"""Tests for the cost-tracking UI surfaces in the evaluations views: the run list's
Cost column, the run detail page's cost breakdown, and the config page's aggregate summary.
"""

from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from apps.cost_tracking.services.reporting import evaluation_run_costs
from apps.evaluations.models import EvaluationRunStatus
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)


@pytest.fixture()
def config(team_with_users):
    return EvaluationConfigFactory.create(team=team_with_users)


@pytest.fixture()
def run(team_with_users, config):
    return EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)


@pytest.mark.django_db()
class TestRunListCostColumn:
    def test_cost_column_shows_run_cost(self, client, team_with_users, config, run):
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

    def test_run_with_no_usage_shows_placeholder(self, client, team_with_users, config, run):
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_table", args=[team_with_users.slug, config.id])

        content = client.get(url).content.decode()

        assert "—" in content

    def test_cost_lookup_is_scoped_to_the_current_page_not_the_whole_config(self, client, team_with_users, config):
        """Regression: cost must be stamped after pagination slices the queryset, not
        before — otherwise every run for the config is loaded and priced on every
        request instead of just the page being rendered (default page size is 25)."""
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
    def test_cost_card_shows_breakdown(self, client, team_with_users, config, run):
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

        assert response.context["run_cost"].total_cost == Decimal("2.50")
        assert evaluator.name in content
        assert "gpt-4o-mini" in content

    def test_avg_cost_per_result_divides_by_total_results(self, client, team_with_users, config, run):
        evaluator = EvaluatorFactory.create(team=team_with_users)
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("4.00"),
            extra={"evaluation_run_id": run.id, "evaluator_id": evaluator.id},
        )
        EvaluationResultFactory.create_batch(4, team=team_with_users, run=run, evaluator=evaluator)
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)

        assert response.context["total_results"] == 4
        assert response.context["avg_cost_per_result"] == Decimal("1.00")

    def test_avg_cost_per_result_absent_when_there_are_no_results(self, client, team_with_users, config, run):
        """Guards the division: a run with cost but zero results (still processing, or every
        evaluator errored) must not raise ZeroDivisionError or render a bogus average."""
        UsageRecordFactory.create(
            team=team_with_users, evaluation_config=config, cost=Decimal("4.00"), extra={"evaluation_run_id": run.id}
        )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)

        assert response.context["total_results"] == 0
        assert "avg_cost_per_result" not in response.context


@pytest.mark.django_db()
class TestConfigAggregateCost:
    def test_placeholder_shown_when_no_cost_recorded(self, client, team_with_users, config, run):
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_results_home", args=[team_with_users.slug, config.id, run.id])

        response = client.get(url)

        assert response.context["run_cost"].by_model == []
        assert "No LLM cost recorded for this run." in response.content.decode()

    def test_summary_shows_all_time_and_last_30_days(self, client, team_with_users, config, run):
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            cost=Decimal("4.20"),
            extra={"evaluation_run_id": run.id},
        )
        client.force_login(team_with_users.members.first())
        url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])

        response = client.get(url)

        assert response.context["cost_summary"].all_time.total_cost == Decimal("4.20")
        assert response.context["cost_summary"].last_30_days.total_cost == Decimal("4.20")
