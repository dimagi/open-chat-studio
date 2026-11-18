import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, QuerySet
from django.utils import timezone as django_timezone

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant
from apps.teams.models import Team
from apps.usage_metrics.buckets import GRANULARITY_TRUNC, Granularity, bucket_key, bucket_start, bucket_starts
from apps.usage_metrics.dashboard_querysets import filtered_querysets
from apps.usage_metrics.filters import HUMAN_AUTHORED

from .models import DashboardCache
from .services import usage_filters

# The engagement charts report on calendar months and ISO weeks in UTC, whatever the viewer's zone.
TZ = ZoneInfo("UTC")
TRAILING_MONTHS = 6
_MONTH = GRANULARITY_TRUNC[Granularity.MONTHLY]
_WEEK = GRANULARITY_TRUNC[Granularity.WEEKLY]

WEEK_BUCKET_KEYS = {1: "1_week", 2: "2_weeks", 3: "3_weeks"}

BREAKDOWN_DIMENSIONS = {
    "chatbot": "chat__experiment_session__experiment__name",
    "channel": "chat__experiment_session__experiment_channel__platform",
}
BREAKDOWN_LIMIT = 8


def _breakdown_label(dimension: str, value: str | None) -> str:
    if not value:
        return "Unknown"
    if dimension == "channel" and value in ChannelPlatform.values:
        return ChannelPlatform(value).label
    return value


def trailing_window(now: datetime | None = None) -> tuple[datetime, datetime, list[date]]:
    """`[start, now)` from the first of the month six months back, with the first of each month in it."""
    now = now or django_timezone.now()
    start = bucket_start(now.astimezone(TZ), Granularity.MONTHLY) - relativedelta(months=TRAILING_MONTHS)
    months = [month.date() for month in bucket_starts(start, now, Granularity.MONTHLY, TZ)]
    return start, now, months


def _human_messages(team: Team, *, start: datetime, end: datetime, filters: dict) -> QuerySet:
    return filtered_querysets(team, start=start, end=end, filters=usage_filters(filters))["messages"].filter(
        HUMAN_AUTHORED
    )


def monthly_activity(team: Team, *, filters: dict, now: datetime | None = None) -> dict[date, dict[int, int]]:
    # Grouping by (participant, month, week) keeps months calendar-accurate while still
    # counting distinct weeks: a week spanning two months is counted once in each.
    start, end, months = trailing_window(now)
    messages = _human_messages(team, start=start, end=end, filters=filters)
    rows = (
        messages.order_by()
        .annotate(month=_MONTH("created_at", tzinfo=TZ), week=_WEEK("created_at", tzinfo=TZ))
        .values("chat__experiment_session__participant_id", "month", "week")
        .distinct()
    )

    month_set = set(months)
    result: dict[date, dict[int, int]] = {month: {} for month in months}
    for row in rows:
        month_key = bucket_key(row["month"], TZ).date()
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

        activity = monthly_activity(self.team, filters=filters, now=now)
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

        activity = monthly_activity(self.team, filters=filters, now=now)
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
            messages.order_by()
            .annotate(week=_WEEK("created_at", tzinfo=TZ))
            .values("chat__experiment_session__participant_id", "week")
            .distinct()
        )

        weekly_participants: dict[date, set[int]] = {
            week.date(): set() for week in bucket_starts(start, end, Granularity.WEEKLY, TZ)
        }
        for row in rows:
            week_start = bucket_key(row["week"], TZ).date()
            if week_start in weekly_participants:
                weekly_participants[week_start].add(row["chat__experiment_session__participant_id"])

        participant_ids = {pid for ids in weekly_participants.values() for pid in ids}
        created_week = {
            p.id: bucket_start(p.created_at.astimezone(TZ), Granularity.WEEKLY).date()
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
        current_month, previous_month = months[-2], months[-3]
        messages = _human_messages(self.team, start=start, end=end, filters=filters)

        rows = (
            messages.annotate(month=_MONTH("created_at", tzinfo=TZ))
            .values("month", field)
            .annotate(participants=Count("chat__experiment_session__participant_id", distinct=True))
        )

        current: dict[str, int] = {}
        previous: dict[str, int] = {}
        for row in rows:
            month = bucket_key(row["month"], TZ).date()
            bucket = current if month == current_month else previous if month == previous_month else None
            if bucket is None:
                continue
            label = _breakdown_label(dimension, row[field])
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
        sessions = filtered_querysets(self.team, start=start, end=end, filters=usage_filters(filters))["sessions"]
        rows = (
            sessions.filter(ended_at__isnull=False)
            .annotate(month=_MONTH("created_at", tzinfo=TZ))
            .values("month")
            .annotate(avg=Avg(ExpressionWrapper(F("ended_at") - F("created_at"), output_field=DurationField())))
        )
        by_month = {bucket_key(row["month"], TZ).date(): row["avg"] for row in rows}

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
