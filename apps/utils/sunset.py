"""Shared machinery for retiring an HTTP surface: one spec per surface, a gate, a view decorator.

See docs/developer_guides/feature_deprecation.md ("HTTP surfaces").
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps

from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils import timezone
from django.utils.timezone import is_naive

from apps.teams.utils import flag_is_active_for_team
from apps.utils.decorators import sunset

FIRST_DETECTION = "first-detection"
MILESTONE_DAYS = (30, 14, 7, 1)


@dataclass(frozen=True)
class HttpSunset:
    key: str
    sunset_at: datetime
    successor_url: str
    enforce_flag: str
    notification_slug: str
    notification_title: str
    build_message: Callable[[list, str], str]
    affected_experiments: Callable[[], QuerySet]
    audience_permissions: tuple[str, ...] = ("bot_channels.add_experimentchannel",)

    def __post_init__(self):
        if is_naive(self.sunset_at):
            raise ValueError("sunset_at must be timezone-aware")


SUNSETS: dict[str, HttpSunset] = {}


def register_sunset(spec: HttpSunset) -> HttpSunset:
    if spec.key in SUNSETS:
        raise ValueError(f"A sunset is already registered under '{spec.key}'")
    SUNSETS[spec.key] = spec
    return spec


def is_enforced(spec: HttpSunset, team) -> bool:
    """True once the sunset date has passed and the spec's flag is active for the team."""
    if not team or timezone.now() < spec.sunset_at:
        return False
    return flag_is_active_for_team(team, spec.enforce_flag)


def sunset_view(spec: HttpSunset, gone: Callable[..., HttpResponse]):
    """Add sunset headers to a view, and answer with `gone` once the spec is enforced for the request's team."""

    def decorator(view_func):
        headers = sunset(spec.sunset_at, successor_url=spec.successor_url)
        with_headers = headers(view_func)
        gone_with_headers = headers(gone)

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if is_enforced(spec, getattr(request, "team", None)):
                return gone_with_headers(request, *args, **kwargs)
            return with_headers(request, *args, **kwargs)

        return wrapper

    return decorator


def due_milestone(spec: HttpSunset, first_detected_at: datetime, now: datetime) -> str | None:
    """The latest milestone that has passed, unless it passed before the team was first notified."""
    passed = [days for days in MILESTONE_DAYS if spec.sunset_at - timedelta(days=days) <= now]
    if not passed:
        return None
    days_before_sunset = min(passed)
    if spec.sunset_at - timedelta(days=days_before_sunset) <= first_detected_at:
        return None
    return f"T-{days_before_sunset}"
