from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from django.utils.timezone import get_current_timezone


def seconds_to_human(value, compact: bool = False):
    """Render a duration in seconds as a human string.

    ``compact=True`` gives a short "2d 3h" / "3h 5m" / "5m 09s" form (at most two units,
    dropping to the next-smaller unit only when the larger one is zero) for space-constrained UI
    like stat rows, instead of the verbose "2 days, 3 hours, ..." default.
    """
    value = int(value)
    days = value // 86400
    hours = (value % 86400) // 3600
    minutes = (value % 3600) // 60
    seconds = value % 60
    if compact:
        return _compact_duration(days, hours, minutes, seconds)
    return _verbose_duration(days, hours, minutes, seconds)


def _compact_duration(days: int, hours: int, minutes: int, seconds: int) -> str:
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds:02d}s"


def _verbose_duration(days: int, hours: int, minutes: int, seconds: int) -> str:
    human_readable = ""
    if days > 0:
        human_readable += f"{days} day{'s' if days > 1 else ''}, "
    if hours > 0 or (days > 0 and (minutes > 0 or seconds > 0)):
        human_readable += f"{hours} hour{'s' if hours > 1 else ''}, "
    if minutes > 0 or (hours > 0 and seconds > 0) or days > 0:
        human_readable += f"{minutes} minute{'s' if minutes > 1 else ''}, "
    human_readable += f"{seconds} second{'s' if seconds > 1 else ''}"
    return human_readable.strip(", ")


def timedelta_to_relative_delta(timedelta: timedelta):
    """Converts a `timedelta` instance to a `relativedelta` instance"""
    return relativedelta(seconds=timedelta.total_seconds())


def resolve_timezone(name: str | None) -> tzinfo:
    """Returns the named zone, falling back to the current timezone for a missing or invalid name."""
    if not name:
        return get_current_timezone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return get_current_timezone()


def pretty_date(date: datetime, as_timezone: str | None = None, include_time: bool = True) -> str:
    """Returns the date like this: 'Monday, 1 January 2024 08:00:00 UTC'.

    Set ``include_time=False`` for a day-precision rendering ('Monday, 1 January 2024').
    """
    date = date.astimezone(resolve_timezone(as_timezone))
    fmt = "%A, %d %B %Y %H:%M:%S %Z" if include_time else "%A, %d %B %Y"
    return date.strftime(fmt)
