from collections import defaultdict

from apps.evaluations.aggregators import aggregate_field

from .models import AnnotationQueueAggregate, AnnotationStatus


def compute_aggregates_for_queue(queue) -> AnnotationQueueAggregate:
    """Compute and store aggregates for all submitted annotations in a queue.

    Groups annotation data by schema field and applies numeric/categorical aggregators.
    """
    field_values = defaultdict(list)
    annotations = queue.items.prefetch_related("annotations").all()

    for item in annotations:
        for ann in item.annotations.all():
            if ann.status != AnnotationStatus.SUBMITTED:
                continue
            for field_name, value in ann.data.items():
                if value is not None:
                    field_values[field_name].append(value)

    agg_data = {field_name: aggregate_field(values) for field_name, values in field_values.items()}

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
