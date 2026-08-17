from collections import defaultdict

from django.db.models import Prefetch

from apps.evaluations.aggregators import aggregate_binary_field, aggregate_field

from .models import Annotation, AnnotationQueueAggregate, AnnotationStatus


def _get_aggregatable_fields(queue) -> set[str]:
    """Return field names that should be included in aggregation (excludes string/text fields)."""
    return {name for name, defn in queue.schema.items() if defn.get("type") != "string"}


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
    """
    aggregatable_fields = _get_aggregatable_fields(queue)
    field_values = defaultdict(list)
    items = queue.items.prefetch_related(
        Prefetch(
            "annotations",
            queryset=Annotation.objects.filter(status=AnnotationStatus.SUBMITTED),
        )
    ).all()

    for item in items:
        submitted = list(item.annotations.all())
        authoritative = [a for a in submitted if a.is_authoritative]
        contributing = authoritative if authoritative else submitted
        for ann in contributing:
            for field_name, value in ann.data.items():
                if field_name in aggregatable_fields and value is not None:
                    field_values[field_name].append(value)

    agg_data = {
        field_name: (
            aggregate_binary_field(values)
            if (queue.schema.get(field_name) or {}).get("type") == "binary"
            else aggregate_field(values)
        )
        for field_name, values in field_values.items()
    }

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
