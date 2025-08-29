from datetime import UTC

from django.db import connection
from django.db.models import Count, functions
from django.utils import timezone

from apps.trace.models import TraceStatus


def get_experiment_error_trend_data(experiment):
    """
    Returns the error count per hour for the last 2 days for an experiment.
    """
    days = 2
    to_date = timezone.now()
    from_date = to_date - timezone.timedelta(days=days)

    # Generate all hour buckets in the range using raw SQL
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT generate_series(
                date_trunc('hour', %s::timestamptz),
                date_trunc('hour', %s::timestamptz),
                interval '1 hour'
            ) as hour_bucket
        """,
            [from_date, to_date],
        )

        hour_buckets = [row[0] for row in cursor.fetchall()]
    # Get error counts for each hour bucket
    error_counts = {}
    error_traces = (
        experiment.traces.filter(status=TraceStatus.ERROR, timestamp__gte=from_date, timestamp__lte=to_date)
        .annotate(hour_bucket=functions.TruncHour("timestamp", tzinfo=UTC))
        .values("hour_bucket")
        .annotate(error_count=Count("id"))
    )

    for trace in error_traces:
        error_counts[trace["hour_bucket"]] = trace["error_count"]

    # Create ordered list with zero-filled gaps
    return [error_counts.get(bucket, 0) for bucket in hour_buckets]
