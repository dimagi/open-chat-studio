from collections import defaultdict
from collections.abc import Callable, Iterator

from django.db.models import Prefetch

from apps.evaluations.aggregators import aggregate_binary_field, aggregate_field

from .models import Annotation, AnnotationQueueAggregate, AnnotationStatus


def _get_aggregatable_fields(queue) -> set[str]:
    """Return field names that should be included in aggregation (excludes string/text fields)."""
    return {name for name, defn in queue.schema.items() if defn.get("type") != "string"}


def _contributing_annotations(queue) -> Iterator[Annotation]:
    """Yield each item's authoritative annotation if it has one, else all its submitted annotations."""
    items = queue.items.prefetch_related(
        Prefetch(
            "annotations",
            queryset=Annotation.objects.filter(status=AnnotationStatus.SUBMITTED),
        )
    ).all()
    for item in items:
        submitted = list(item.annotations.all())
        authoritative = [a for a in submitted if a.is_authoritative]
        yield from authoritative or submitted


def _collect_field_values(queue) -> tuple[dict[str, list], dict[str, list], dict[str, list]]:
    """Return aggregatable field values as (all, from authoritative annotations, from the rest)."""
    aggregatable_fields = _get_aggregatable_fields(queue)
    field_values = defaultdict(list)
    authoritative_values = defaultdict(list)
    unresolved_values = defaultdict(list)
    for ann in _contributing_annotations(queue):
        split_values = authoritative_values if ann.is_authoritative else unresolved_values
        for field_name, value in ann.data.items():
            if field_name in aggregatable_fields and value is not None:
                field_values[field_name].append(value)
                split_values[field_name].append(value)
    return field_values, authoritative_values, unresolved_values


def _add_count_split(stats: dict, aggregate: Callable[[list], dict], authoritative: list, unresolved: list) -> None:
    """Add `authoritative_count` and `unresolved_count` to `stats` when they add up to its `count`."""
    authoritative_count = aggregate(authoritative).get("count", 0)
    unresolved_count = aggregate(unresolved).get("count", 0)
    # With mixed value types, a subset can settle on a different type than the full list and count
    # values the total excluded.
    if authoritative_count + unresolved_count == stats.get("count"):
        stats["authoritative_count"] = authoritative_count
        stats["unresolved_count"] = unresolved_count


def compute_aggregates_for_queue(queue) -> AnnotationQueueAggregate:
    """Compute and store aggregates for all submitted annotations in a queue.

    Per item: use authoritative annotation if one exists, else fall back to all
    submitted annotations. Fields are aggregated per the schema: binary fields
    dispatch to `aggregate_binary_field`, everything else to the numeric /
    categorical `aggregate_field`. Text (string) fields are excluded from
    aggregation.

    Unlike the evaluation-run side (`apps.evaluations.aggregation`), which gates
    value collection on `get_aggregators_for_value` before dispatch, this function
    collects any non-None value regardless of shape. A value of an unsupported
    shape (a dict or list) therefore reaches `aggregate_binary_field` and is
    counted in `excluded_count`, where the evaluation-run side would have dropped
    it before it was ever counted.

    Each field's stats also carry `authoritative_count` and `unresolved_count`,
    splitting `count` by whether the value came from an authoritative annotation
    (omitted when mixed value types make the two disagree with `count`).
    """
    field_values, authoritative_values, unresolved_values = _collect_field_values(queue)

    agg_data = {}
    for field_name, values in field_values.items():
        is_binary = (queue.schema.get(field_name) or {}).get("type") == "binary"
        aggregate = aggregate_binary_field if is_binary else aggregate_field
        stats = aggregate(values)
        _add_count_split(stats, aggregate, authoritative_values[field_name], unresolved_values[field_name])
        agg_data[field_name] = stats

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
