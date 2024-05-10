from datetime import timedelta

from dateutil.relativedelta import relativedelta


def seconds_to_human(value):
    value = int(value)
    days = value // 86400
    hours = (value % 86400) // 3600
    minutes = (value % 3600) // 60
    seconds = value % 60
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
