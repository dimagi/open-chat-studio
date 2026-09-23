import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, QuerySet
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone as django_timezone

from apps.experiments.models import Participant
from apps.teams.models import Team
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.usage_metrics.filters import HUMAN_AUTHORED
from apps.usage_metrics.metrics import bucket_date

from .models import DashboardCache

TZ = ZoneInfo("UTC")
TRAILING_MONTHS = 6

WEEK_BUCKET_KEYS = {1: "1_week", 2: "2_weeks", 3: "3_weeks"}

BREAKDOWN_DIMENSIONS = {
    "chatbot": "chat__experiment_session__experiment__name",
    "channel": "chat__experiment_session__experiment_channel__platform",
}
BREAKDOWN_LIMIT = 8


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

    def get_engagement_summary_data(self, now: datetime | None = None, **filters) -> list[dict[str, Any]]:
        cache_key = f"engagement_summary_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        activity = weekly_activity_by_month(self.team, filters=filters, now=now)
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

    def get_engagement_frequency_data(self, now: datetime | None = None, **filters) -> list[dict[str, Any]]:
        cache_key = f"engagement_frequency_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        activity = weekly_activity_by_month(self.team, filters=filters, now=now)
        current_month = max(activity)
        data = []
        for month in sorted(activity):
            buckets = {"1_week": 0, "2_weeks": 0, "3_weeks": 0, "4_plus_weeks": 0}
            for weeks in activity[month].values():
                buckets[WEEK_BUCKET_KEYS.get(weeks, "4_plus_weeks")] += 1
            data.append({"month": month.isoformat(), **buckets, "in_progress": month == current_month})

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_new_vs_returning_data(self, now: datetime | None = None, **filters) -> list[dict[str, Any]]:
        cache_key = f"new_vs_returning_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        start, end, _months = trailing_window(now)
        messages = _human_messages(self.team, start=start, end=end, filters=filters)
        rows = (
            messages.annotate(week=TruncWeek("created_at", tzinfo=TZ))
            .values("chat__experiment_session__participant_id", "week")
            .distinct()
        )

        weekly_participants: dict[date, set[int]] = {}
        week = _week_start(start)
        last_week = _week_start(end)
        while week <= last_week:
            weekly_participants[week] = set()
            week += timedelta(days=7)

        for row in rows:
            week_start = bucket_date(row["week"], TZ)
            if week_start in weekly_participants:
                weekly_participants[week_start].add(row["chat__experiment_session__participant_id"])

        participant_ids = {pid for ids in weekly_participants.values() for pid in ids}
        created_week = {
            p.id: _week_start(p.created_at)
            for p in Participant.objects.filter(team=self.team, id__in=participant_ids).only("id", "created_at")
        }

        data = []
        for week in sorted(weekly_participants):
            active_ids = weekly_participants[week]
            new_count = sum(1 for pid in active_ids if created_week.get(pid) == week)
            data.append({"week": week.isoformat(), "new": new_count, "returning": len(active_ids) - new_count})

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_engagement_breakdown(self, dimension: str, now: datetime | None = None, **filters) -> list[dict[str, Any]]:
        """Active participants this month by chatbot or channel, ranked, with last month for comparison.

        Answers "which of these is driving the headline number", which the aggregate tiles cannot.
        """
        field = BREAKDOWN_DIMENSIONS[dimension]
        cache_key = f"engagement_breakdown_{dimension}_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        start, end, months = trailing_window(now)
        current_month, previous_month = months[-1], months[-2]
        messages = _human_messages(self.team, start=start, end=end, filters=filters)

        rows = (
            messages.annotate(month=TruncMonth("created_at", tzinfo=TZ))
            .values("month", field)
            .annotate(participants=Count("chat__experiment_session__participant_id", distinct=True))
        )

        current: dict[str, int] = {}
        previous: dict[str, int] = {}
        for row in rows:
            month = bucket_date(row["month"], TZ)
            bucket = current if month == current_month else previous if month == previous_month else None
            if bucket is None:
                continue
            label = row[field] or "Unknown"
            bucket[label] = bucket.get(label, 0) + row["participants"]

        total = sum(current.values())
        data = [
            {
                "label": label,
                "participants": participants,
                "share": (participants / total * 100) if total else 0,
                "previous": previous.get(label, 0),
            }
            for label, participants in sorted(current.items(), key=lambda item: (-item[1], item[0]))
        ][:BREAKDOWN_LIMIT]

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_average_session_duration(self, now: datetime | None = None, **filters) -> list[dict[str, Any]]:
        """Mean completed-session length per month, so the stat tile can show a trend and a delta."""
        # v2: payload changed from a single float to a monthly series -- a new key so warm
        # caches from the previous shape are never served to a client expecting the list.
        cache_key = f"engagement_avg_session_duration_v2_{_cache_key(filters)}"
        cached = DashboardCache.get_cached_data(self.team, cache_key)
        if cached is not None:
            return cached

        start, end, months = trailing_window(now)
        sessions = filtered_querysets(self.team, start_date=start, end_date=end, **filters)["sessions"]
        rows = (
            sessions.filter(ended_at__isnull=False)
            .annotate(month=TruncMonth("created_at", tzinfo=TZ))
            .values("month")
            .annotate(avg=Avg(ExpressionWrapper(F("ended_at") - F("created_at"), output_field=DurationField())))
        )
        by_month = {bucket_date(row["month"], TZ): row["avg"] for row in rows}

        current_month = months[-1]
        data = [
            {
                "month": month.isoformat(),
                "minutes": (by_month.get(month) or timedelta()).total_seconds() / 60,
                "in_progress": month == current_month,
            }
            for month in months
        ]

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data
