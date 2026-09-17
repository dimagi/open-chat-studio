import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import QuerySet
from django.db.models.functions import TruncWeek
from django.utils import timezone as django_timezone

from apps.teams.models import Team
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.usage_metrics.filters import HUMAN_AUTHORED
from apps.usage_metrics.metrics import bucket_date

from .models import DashboardCache

TZ = ZoneInfo("UTC")
TRAILING_MONTHS = 6

WEEK_BUCKET_KEYS = {1: "1_week", 2: "2_weeks", 3: "3_weeks"}


def _add_months(d: date, delta: int) -> date:
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _week_start(dt: datetime) -> date:
    local = dt.astimezone(TZ)
    return local.date() - timedelta(days=local.weekday())


def trailing_window(now: datetime | None = None) -> tuple[datetime, datetime, list[date]]:
    now = now or django_timezone.now()
    now_local = now.astimezone(TZ)
    current_month_start = date(now_local.year, now_local.month, 1)
    months = [_add_months(current_month_start, -offset) for offset in range(TRAILING_MONTHS, -1, -1)]
    start = datetime(months[0].year, months[0].month, months[0].day, tzinfo=TZ)
    return start, now, months


def _human_messages(team: Team, *, start: datetime, end: datetime, filters: dict) -> QuerySet:
    return filtered_querysets(team, start_date=start, end_date=end, **filters)["messages"].filter(HUMAN_AUTHORED)


def weekly_activity_by_month(team: Team, *, filters: dict, now: datetime | None = None) -> dict[date, dict[int, int]]:
    start, end, months = trailing_window(now)
    messages = _human_messages(team, start=start, end=end, filters=filters)
    rows = (
        messages.annotate(week=TruncWeek("created_at", tzinfo=TZ))
        .values("chat__experiment_session__participant_id", "week")
        .distinct()
    )

    month_set = set(months)
    result: dict[date, dict[int, int]] = {month: {} for month in months}
    for row in rows:
        week_start = bucket_date(row["week"], TZ)
        month_key = date(week_start.year, week_start.month, 1)
        if month_key not in month_set:
            continue
        participant_id = row["chat__experiment_session__participant_id"]
        result[month_key][participant_id] = result[month_key].get(participant_id, 0) + 1

    return result


def _cache_key(filters: dict) -> str:
    def normalize(obj):
        if isinstance(obj, dict):
            return {k: normalize(obj[k]) for k in sorted(obj)}
        if isinstance(obj, list):
            return sorted(normalize(v) for v in obj)
        return obj

    normalized = normalize(filters or {})
    json_str = json.dumps(normalized, separators=(",", ":"), sort_keys=True, cls=DjangoJSONEncoder)
    return hashlib.sha1(json_str.encode()).hexdigest()


class EngagementDashboardService:
    def __init__(self, team: Team):
        self.team = team

    def get_engagement_summary_data(self, **filters) -> list[dict[str, Any]]:
        cache_key = f"engagement_summary_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        activity = weekly_activity_by_month(self.team, filters=filters)
        current_month = max(activity)
        data = []
        for month in sorted(activity):
            participants = activity[month]
            mau = len(participants)
            core_users = sum(1 for weeks in participants.values() if weeks >= 2)
            data.append(
                {
                    "month": month.isoformat(),
                    "mau": mau,
                    "core_users_rate": (core_users / mau * 100) if mau else 0,
                    "in_progress": month == current_month,
                }
            )

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data
