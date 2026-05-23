from collections import defaultdict

from django.db.models import Prefetch

from apps.evaluations.aggregators import aggregate_field

from .models import Annotation, AnnotationQueueAggregate, AnnotationStatus


def _get_aggregatable_fields(queue) -> set[str]:
    """Return field names that should be included in aggregation (excludes string/text fields)."""
    return {name for name, defn in queue.schema.items() if defn.get("type") != "string"}


def compute_aggregates_for_queue(queue) -> AnnotationQueueAggregate:
    """Compute and store aggregates for all submitted annotations in a queue.

    Per item: use authoritative annotation if one exists, else fall back to all
    submitted annotations. Numeric / categorical aggregators are applied per field.
    Text (string) fields are excluded from aggregation.
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

    agg_data = {field_name: aggregate_field(values) for field_name, values in field_values.items()}

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
