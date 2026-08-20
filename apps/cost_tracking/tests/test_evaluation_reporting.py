"""Tests for the evaluation-scoped cost read path (`services/reporting.py`):
`evaluation_run_cost`, `evaluation_run_costs`, `evaluation_config_cost_summary`.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.cost_tracking.models import Confidence
from apps.cost_tracking.services.reporting import (
    evaluation_config_cost_summary,
    evaluation_run_cost,
    evaluation_run_costs,
)
from apps.utils.factories.cost_tracking import PricingRuleFactory, UsageRecordFactory
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationRunFactory, EvaluatorFactory

# A model name absent from the seed JSON, so a global PricingRule for it can't
# collide with the rows migration 0062_load_ai_pricing inserts. Tests run with
# --no-migrations locally and on PRs, so a seeded name only breaks on main.
_PRICED_MODEL = "test-priced-model"


@pytest.mark.django_db()
def test_evaluation_run_cost_breaks_down_by_evaluator_and_model():
    config = EvaluationConfigFactory.create()
    run = EvaluationRunFactory.create(team=config.team, config=config)
    evaluator = EvaluatorFactory.create(team=config.team)
    other_evaluator = EvaluatorFactory.create(team=config.team)

    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="openai",
        model_name="gpt-4o-mini",
        cost=Decimal("0.10"),
        quantity=100,
        extra={"evaluation_run_id": run.id, "evaluator_id": evaluator.id},
    )
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="openai",
        model_name="gpt-4o-mini",
        cost=Decimal("0.05"),
        quantity=50,
        extra={"evaluation_run_id": run.id, "evaluator_id": other_evaluator.id},
    )
    # Bot generation carries no evaluator_id — it's the run's other half of spend.
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="anthropic",
        model_name="claude-3",
        cost=Decimal("0.20"),
        quantity=200,
        extra={"evaluation_run_id": run.id},
    )
    # A different run in the same config must not leak into this run's total.
    other_run = EvaluationRunFactory.create(team=config.team, config=config)
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("999"), extra={"evaluation_run_id": other_run.id}
    )

    result = evaluation_run_cost(run)

    assert result.total_cost == Decimal("0.35")
    assert result.total_tokens == 350
    assert result.currency == "USD"
    assert {row.evaluator_name: row.cost for row in result.by_evaluator} == {
        evaluator.name: Decimal("0.10"),
        other_evaluator.name: Decimal("0.05"),
        "Bot generation": Decimal("0.20"),
    }
    assert {row.model_name: row.cost for row in result.by_model} == {
        "gpt-4o-mini": Decimal("0.15"),
        "claude-3": Decimal("0.20"),
    }


@pytest.mark.django_db()
def test_evaluation_run_cost_with_no_usage_returns_zero():
    config = EvaluationConfigFactory.create()
    run = EvaluationRunFactory.create(team=config.team, config=config)

    result = evaluation_run_cost(run)

    assert result.total_cost == Decimal(0)
    assert result.total_tokens == 0
    assert result.has_unpriced is False
    assert result.has_estimated is False
    assert result.has_unknown is False
    assert result.by_evaluator == []
    assert result.by_model == []


@pytest.mark.django_db()
def test_evaluation_run_cost_confidence_flags_roll_up_from_rows():
    config = EvaluationConfigFactory.create()
    run = EvaluationRunFactory.create(team=config.team, config=config)
    evaluator = EvaluatorFactory.create(team=config.team)
    priced_rule = PricingRuleFactory.create(provider_type="openai", model_name=_PRICED_MODEL)

    # Priced, exact — the "clean" row, judged by `evaluator`.
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="openai",
        model_name=_PRICED_MODEL,
        cost=Decimal("0.10"),
        pricing_rule=priced_rule,
        extra={"evaluation_run_id": run.id, "evaluator_id": evaluator.id},
    )
    # Estimated confidence, same model/evaluator.
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="openai",
        model_name=_PRICED_MODEL,
        cost=Decimal("0.05"),
        pricing_rule=priced_rule,
        confidence=Confidence.ESTIMATED,
        extra={"evaluation_run_id": run.id, "evaluator_id": evaluator.id},
    )
    # Unpriced + unknown, from bot generation (no evaluator_id).
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        provider_type="anthropic",
        model_name="claude-3",
        cost=Decimal("0"),
        pricing_rule=None,
        confidence=Confidence.UNKNOWN,
        extra={"evaluation_run_id": run.id},
    )

    result = evaluation_run_cost(run)

    assert result.has_unpriced is True
    assert result.has_estimated is True
    assert result.has_unknown is True

    by_model = {row.model_name: row for row in result.by_model}
    assert by_model[_PRICED_MODEL].has_unpriced is False
    assert by_model[_PRICED_MODEL].has_estimated is True
    assert by_model[_PRICED_MODEL].has_unknown is False
    assert by_model["claude-3"].has_unpriced is True
    assert by_model["claude-3"].has_unknown is True

    by_evaluator = {row.evaluator_name: row for row in result.by_evaluator}
    assert by_evaluator[evaluator.name].has_unpriced is False
    assert by_evaluator[evaluator.name].has_estimated is True
    assert by_evaluator["Bot generation"].has_unpriced is True
    assert by_evaluator["Bot generation"].has_unknown is True


@pytest.mark.django_db()
def test_evaluation_run_costs_scopes_to_requested_runs():
    config = EvaluationConfigFactory.create()
    run1 = EvaluationRunFactory.create(team=config.team, config=config)
    run2 = EvaluationRunFactory.create(team=config.team, config=config)
    unrequested_run = EvaluationRunFactory.create(team=config.team, config=config)

    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("1"), extra={"evaluation_run_id": run1.id}
    )
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("2"), extra={"evaluation_run_id": run1.id}
    )
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("3"), extra={"evaluation_run_id": run2.id}
    )
    UsageRecordFactory.create(
        team=config.team,
        evaluation_config=config,
        cost=Decimal("100"),
        extra={"evaluation_run_id": unrequested_run.id},
    )

    result = evaluation_run_costs(config.id, [run1.id, run2.id])

    assert result == {run1.id: Decimal("3"), run2.id: Decimal("3")}


@pytest.mark.django_db()
def test_evaluation_run_costs_with_no_run_ids_returns_empty_dict():
    assert evaluation_run_costs(1, []) == {}


@pytest.mark.django_db()
def test_evaluation_config_cost_summary_splits_last_30_days_from_all_time():
    config = EvaluationConfigFactory.create()
    now = timezone.now()
    UsageRecordFactory.create(team=config.team, evaluation_config=config, cost=Decimal("1"), at=now - timedelta(days=1))
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("5"), at=now - timedelta(days=45)
    )
    # A different config's spend must not be counted.
    other_config = EvaluationConfigFactory.create()
    UsageRecordFactory.create(team=other_config.team, evaluation_config=other_config, cost=Decimal("999"))

    summary = evaluation_config_cost_summary(config)

    assert summary.last_30_days.total_cost == Decimal("1")
    assert summary.all_time.total_cost == Decimal("6")


@pytest.mark.django_db()
def test_evaluation_config_cost_summary_confidence_flags_are_scoped_per_period():
    """A row unpriced 45 days ago must not mark the 30-day window unpriced — each
    period's flags come from its own conditional count, not the config's whole history."""
    config = EvaluationConfigFactory.create()
    now = timezone.now()
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("0"), pricing_rule=None, at=now - timedelta(days=45)
    )
    rule = PricingRuleFactory.create()
    UsageRecordFactory.create(
        team=config.team, evaluation_config=config, cost=Decimal("1"), pricing_rule=rule, at=now - timedelta(days=1)
    )

    summary = evaluation_config_cost_summary(config)

    assert summary.last_30_days.has_unpriced is False
    assert summary.all_time.has_unpriced is True


@pytest.mark.django_db()
def test_evaluation_config_cost_summary_with_no_usage_is_zero():
    config = EvaluationConfigFactory.create()

    summary = evaluation_config_cost_summary(config)

    assert summary.last_30_days.total_cost == Decimal(0)
    assert summary.all_time.total_cost == Decimal(0)
    assert summary.last_30_days.has_unpriced is False
    assert summary.all_time.has_unpriced is False
