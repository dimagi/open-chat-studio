from collections import defaultdict

from apps.evaluations.aggregators import aggregate_field, get_aggregators_for_value
from apps.evaluations.models import EvaluationRun, EvaluationRunAggregate


def compute_aggregates_for_run(run: EvaluationRun) -> list[EvaluationRunAggregate]:
    """
    Compute and store aggregates for all evaluators in a completed run.
    Returns list of created/updated EvaluationRunAggregate objects.
    """
    results_by_evaluator = defaultdict(list)

    for result in run.results.select_related("evaluator").all():
        result_data = result.output.get("result")
        if result_data:  # Skip results with errors
            results_by_evaluator[result.evaluator_id].append(result_data)

    aggregates = []
    for evaluator_id, results in results_by_evaluator.items():
        agg_data = compute_evaluator_aggregates(results)

        obj, _ = EvaluationRunAggregate.objects.update_or_create(
            run=run,
            evaluator_id=evaluator_id,
            defaults={"aggregates": agg_data},
        )
        aggregates.append(obj)

    return aggregates


def compute_evaluator_aggregates(results: list[dict]) -> dict:
    """Compute aggregates for a single evaluator's results."""
    if not results:
        return {}

    field_values = defaultdict(list)
    for result in results:
        for field_name, value in result.items():
            if value is not None and get_aggregators_for_value(value):
                field_values[field_name].append(value)

    return {field_name: aggregate_field(values) for field_name, values in field_values.items()}
