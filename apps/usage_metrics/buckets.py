"""One granularity vocabulary for every bucketed usage read.

A bucket is named by its tz-aware start. `bucket_starts` enumerates the starts
covering a window, `by_bucket` groups a queryset by the DB's truncation of a
timestamp column, and `bucket_key` turns what the DB hands back into the same
start, so the two sides join on equal instants.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.db.models import QuerySet, TextChoices
from django.db.models.functions import TruncDate, TruncHour, TruncMonth, TruncWeek
from django.utils.translation import gettext_lazy as _


class Granularity(TextChoices):
    HOURLY = "hourly", _("Hourly")
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")
    MONTHLY = "monthly", _("Monthly")


# DB truncation per granularity. Django's TruncWeek starts weeks on Monday, as `bucket_start` does.
GRANULARITY_TRUNC = {
    Granularity.HOURLY: TruncHour,
    Granularity.DAILY: TruncDate,
    Granularity.WEEKLY: TruncWeek,
    Granularity.MONTHLY: TruncMonth,
}

_CALENDAR_STEP = {
    Granularity.DAILY: relativedelta(days=1),
    Granularity.WEEKLY: relativedelta(weeks=1),
    Granularity.MONTHLY: relativedelta(months=1),
}


def bucket_start(moment: datetime, granularity: str) -> datetime:
    """`moment` moved back to the start of its bucket, on its own wall clock."""
    granularity = Granularity(granularity)
    if granularity == Granularity.HOURLY:
        return moment.replace(minute=0, second=0, microsecond=0)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == Granularity.WEEKLY:
        return midnight - timedelta(days=midnight.weekday())
    if granularity == Granularity.MONTHLY:
        return midnight.replace(day=1)
    return midnight


def bucket_starts(start: datetime, end: datetime, granularity: str, tz: ZoneInfo):
    """Yield the tz-aware bucket starts covering `[start, end)`, oldest first.

    The first is `start` moved back to its bucket boundary in `tz`, so it may precede `start` for an
    unaligned window; a query over the window still counts activity from `start` on. Days, weeks and
    months step on the wall clock, so midnight stays midnight across a DST change; hours step in
    elapsed time."""
    granularity = Granularity(granularity)
    cursor = bucket_start(start.astimezone(tz), granularity)
    while cursor < end:
        yield cursor
        if granularity == Granularity.HOURLY:
            cursor = (cursor.astimezone(UTC) + timedelta(hours=1)).astimezone(tz)
        else:
            cursor = cursor + _CALENDAR_STEP[granularity]


def bucket_key(value: date | datetime, tz: ZoneInfo) -> datetime:
    """The tz-aware bucket start a DB truncation names: `TruncDate` yields a date, the others an aware datetime."""
    if isinstance(value, datetime):
        return value.astimezone(tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def by_bucket(
    queryset: QuerySet,
    granularity: str,
    tz: ZoneInfo,
    *,
    field: str = "created_at",
    group_by: str | None = None,
    **annotations,
):
    """Group `queryset` into tz-truncated `field` buckets, and by `group_by` if given; yields `(bucket start, row)`."""
    trunc = GRANULARITY_TRUNC[Granularity(granularity)]
    group_fields = ["bucket", group_by] if group_by else ["bucket"]
    rows = queryset.annotate(bucket=trunc(field, tzinfo=tz)).order_by().values(*group_fields).annotate(**annotations)
    for row in rows:
        yield bucket_key(row["bucket"], tz), row
