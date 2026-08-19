from collections import defaultdict

from apps.evaluations.aggregators import aggregate_binary_field, aggregate_field, get_aggregators_for_value
from apps.evaluations.models import EvaluationRun, EvaluationRunAggregate, Evaluator

RESULT_CHUNK_SIZE = 500  # results fetched per round trip when streaming a run's results


def compute_aggregates_for_run(run: EvaluationRun) -> list[EvaluationRunAggregate]:
    """
    Compute and store aggregates for all evaluators in a completed run.
    Returns list of created/updated EvaluationRunAggregate objects.

    Streams the results and keeps only the values the aggregators need, so peak memory
    scales with the number of distinct result fields rather than with the number of
    results. Holding every result's parsed `output` at once made a few-thousand-message
    run large enough to OOM the worker.
    """
    field_values_by_evaluator: defaultdict[int, defaultdict[str, list]] = defaultdict(lambda: defaultdict(list))

    rows = run.results.values_list("evaluator_id", "output")
    for evaluator_id, output in rows.iterator(chunk_size=RESULT_CHUNK_SIZE):
        result_data = (output or {}).get("result")
        if result_data:  # Skip results with errors
            _collect_aggregatable_values(result_data, field_values_by_evaluator[evaluator_id])

    # One bulk fetch for the schemas; EvaluationResult.evaluator is CASCADE so an id
    # from results always resolves outside a delete race.
    evaluators = Evaluator.objects.in_bulk(field_values_by_evaluator)

    aggregates = []
    for evaluator_id, field_values in field_values_by_evaluator.items():
        evaluator = evaluators.get(evaluator_id)
        schema = (evaluator.params or {}).get("output_schema", {}) or {} if evaluator else {}
        obj, _ = EvaluationRunAggregate.objects.update_or_create(
            run=run,
            evaluator_id=evaluator_id,
            defaults={"aggregates": _aggregate_fields(field_values, schema)},
        )
        aggregates.append(obj)

    return aggregates


def _collect_aggregatable_values(result: dict, field_values: defaultdict[str, list]) -> None:
    """Accumulate one result's aggregatable values into `field_values`, keyed by field name."""
    for field_name, value in result.items():
        if value is not None and get_aggregators_for_value(value):
            field_values[field_name].append(value)


def _aggregate_fields(field_values: defaultdict[str, list], schema: dict) -> dict:
    """Aggregate each field, dispatching binary fields by schema type.

    A field name present in results but absent from the schema (renamed, removed,
    or a Python evaluator with no output_schema) falls through to aggregate_field.
    """
    return {
        field_name: (
            aggregate_binary_field(values)
            if (schema.get(field_name) or {}).get("type") == "binary"
            else aggregate_field(values)
        )
        for field_name, values in field_values.items()
    }
