"""Read path for evaluation-scoped cost reporting.

Judge calls and eval-driven bot generation both write `UsageRecord` rows with
`source=EVALUATION` and `evaluation_config` set (see `apps/evaluations/usage.py` and
ADR-0048/0050). This module aggregates those rows per run and per config for the
evaluations UI; the dashboard-facing aggregates in `apps/cost_tracking/services/reporting.py`
deliberately exclude evaluation spend from per-entity reads, so evaluation surfaces need
their own read path rather than reusing those functions.

Every read here is scoped by the indexed `evaluation_config` FK first; `extra.evaluation_run_id`
(a JSON key, not a FK, since runs get pruned) only narrows within that.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.cost_tracking.models import UsageRecord
from apps.evaluations.models import EvaluationConfig, EvaluationRun, Evaluator

_ZERO = Decimal(0)
_COST_FIELD = DecimalField(max_digits=14, decimal_places=8)


@dataclass(frozen=True)
class ModelSpend:
    provider_type: str
    model_name: str
    cost: Decimal
    tokens: int


@dataclass(frozen=True)
class EvaluatorSpend:
    """One row of a run's cost broken down by what incurred it: an evaluator's judge
    calls, or (`evaluator_id=None`) the bot generation the run drove."""

    evaluator_id: int | None
    evaluator_name: str
    cost: Decimal
    tokens: int


@dataclass(frozen=True)
class EvaluationRunCost:
    total_cost: Decimal
    total_tokens: int
    currency: str
    by_evaluator: list[EvaluatorSpend] = field(default_factory=list)
    by_model: list[ModelSpend] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationConfigCostSummary:
    last_30_days: Decimal
    all_time: Decimal
    currency: str


def evaluation_run_cost(run: EvaluationRun) -> EvaluationRunCost:
    """Total cost for one run, broken down by (provider, model) and by evaluator.

    A single query over rows scoped to the run's config and `extra.evaluation_run_id`;
    both breakdowns are built from the same fetch so they always reconcile with the total.
    """
    rows = list(
        UsageRecord.objects.filter(evaluation_config_id=run.config_id, extra__evaluation_run_id=run.id).values_list(
            "provider_type", "model_name", "extra", "cost", "quantity", "currency"
        )
    )
    if not rows:
        return EvaluationRunCost(total_cost=_ZERO, total_tokens=0, currency="USD")

    model_totals: dict[tuple[str, str], dict] = defaultdict(lambda: {"cost": _ZERO, "tokens": 0})
    evaluator_totals: dict[int | None, dict] = defaultdict(lambda: {"cost": _ZERO, "tokens": 0})
    total_cost = _ZERO
    total_tokens = 0
    currencies = set()

    for provider_type, model_name, extra, cost, quantity, currency in rows:
        tokens = int(quantity or 0)
        total_cost += cost
        total_tokens += tokens
        currencies.add(currency)

        model_bucket = model_totals[(provider_type, model_name)]
        model_bucket["cost"] += cost
        model_bucket["tokens"] += tokens

        evaluator_bucket = evaluator_totals[(extra or {}).get("evaluator_id")]
        evaluator_bucket["cost"] += cost
        evaluator_bucket["tokens"] += tokens

    evaluator_names = dict(
        Evaluator.objects.filter(id__in=[k for k in evaluator_totals if k is not None]).values_list("id", "name")
    )

    by_model = sorted(
        (
            ModelSpend(provider_type=provider_type, model_name=model_name, cost=agg["cost"], tokens=agg["tokens"])
            for (provider_type, model_name), agg in model_totals.items()
        ),
        key=lambda row: row.cost,
        reverse=True,
    )
    by_evaluator = sorted(
        (
            EvaluatorSpend(
                evaluator_id=evaluator_id,
                evaluator_name=evaluator_names.get(evaluator_id, f"Evaluator {evaluator_id}")
                if evaluator_id is not None
                else "Bot generation",
                cost=agg["cost"],
                tokens=agg["tokens"],
            )
            for evaluator_id, agg in evaluator_totals.items()
        ),
        key=lambda row: row.cost,
        reverse=True,
    )
    currency = currencies.pop() if len(currencies) == 1 else "USD"
    return EvaluationRunCost(
        total_cost=total_cost,
        total_tokens=total_tokens,
        currency=currency,
        by_evaluator=by_evaluator,
        by_model=by_model,
    )


def evaluation_run_costs(config_id: int, run_ids: list[int]) -> dict[int, Decimal]:
    """Total cost per run, keyed by run id, for a page of runs in one config.

    Filters on both the indexed `evaluation_config` FK and `extra.evaluation_run_id__in`
    so the scan is bounded to the runs asked for, not the whole config's history.
    """
    if not run_ids:
        return {}
    totals: dict[int, Decimal] = defaultdict(lambda: _ZERO)
    rows = UsageRecord.objects.filter(evaluation_config_id=config_id, extra__evaluation_run_id__in=run_ids).values_list(
        "extra", "cost"
    )
    for extra, cost in rows:
        run_id = (extra or {}).get("evaluation_run_id")
        if run_id is not None:
            totals[run_id] += cost
    return dict(totals)


def evaluation_config_cost_summary(config: EvaluationConfig) -> EvaluationConfigCostSummary:
    """Aggregate spend across every run of one config: last 30 days and all time.

    Single aggregate query (two conditional sums) over the indexed `evaluation_config` FK.
    """
    qs = UsageRecord.objects.filter(evaluation_config_id=config.id)
    cutoff = timezone.now() - timedelta(days=30)
    agg = qs.aggregate(
        all_time=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
        last_30_days=Coalesce(Sum("cost", filter=Q(timestamp__gte=cutoff)), _ZERO, output_field=_COST_FIELD),
    )
    currencies = list(qs.values_list("currency", flat=True).distinct())
    currency = currencies[0] if len(currencies) == 1 else "USD"
    return EvaluationConfigCostSummary(last_30_days=agg["last_30_days"], all_time=agg["all_time"], currency=currency)
