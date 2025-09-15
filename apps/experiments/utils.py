from datetime import UTC

from django.db.models import Case, Count, When, functions
from django.utils import timezone

from apps.trace.models import TraceStatus


def get_experiment_trend_data(experiment) -> tuple[list[int], list[int]]:
    """
    Returns the error count per hour for the last 2 days for an experiment.
    """
    days = 2
    to_date = timezone.now()
    from_date = to_date - timezone.timedelta(days=days)

    # Get error counts for each hour bucket
    error_trend = {}
    success_trend = {}
    trace_counts = (
        experiment.traces.filter(timestamp__gte=from_date, timestamp__lte=to_date)
        .annotate(hour_bucket=functions.TruncHour("timestamp", tzinfo=UTC))
        .values("hour_bucket")
        .annotate(
            error_count=Count(Case(When(status=TraceStatus.ERROR, then=1))),
            success_count=Count(Case(When(status=TraceStatus.SUCCESS, then=1))),
        )
    )

    for trace in trace_counts:
        error_trend[trace["hour_bucket"]] = trace["error_count"]
        success_trend[trace["hour_bucket"]] = trace["success_count"]

    # Create ordered list with zero-filled gaps
    hour_buckets = []
    current = from_date.replace(minute=0, second=0, microsecond=0)
    end = to_date.replace(minute=0, second=0, microsecond=0)

    while current <= end:
        hour_buckets.append(current)
        current += timezone.timedelta(hours=1)

    successes = [success_trend.get(bucket, 0) for bucket in hour_buckets]
    errors = [error_trend.get(bucket, 0) for bucket in hour_buckets]
    return successes, errors
