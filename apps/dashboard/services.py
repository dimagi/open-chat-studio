from datetime import datetime, timedelta
from typing import Any

from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncDate, TruncHour, TruncMonth, TruncWeek
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, Participant

from ..annotations.models import TagCategories
from ..trace.models import Trace
from .models import DashboardCache


class DashboardService:
    """Service class for dashboard analytics operations"""

    GRANULARITY_TRUNC_MAP = {
        "hourly": TruncHour,
        "daily": TruncDate,
        "weekly": TruncWeek,
        "monthly": TruncMonth,
    }

    VALID_ORDER_FIELDS = [
        "participants",
        "sessions",
        "messages",
        "completion_rate",
        "avg_session_duration",
        "avg_messages_per_session",
    ]

    def __init__(self, team):
        self.team = team

    def get_filtered_queryset_base(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        experiment_ids: list[int] | None = None,
        platform_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get base querysets with common filters applied"""

        # Default date range (last 30 days)
        if not end_date:
            end_date = timezone.now()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        base_filters = {"created_at__gte": start_date, "created_at__lte": end_date}

        # Base querysets
        experiments = Experiment.objects.filter(team=self.team, is_archived=False, working_version=None)
        sessions = ExperimentSession.objects.filter(
            team=self.team, chat__messages__created_at__gte=start_date, chat__messages__created_at__lte=end_date
        ).distinct()
        messages = ChatMessage.objects.filter(chat__team=self.team, **base_filters)
        participants = Participant.objects.filter(team=self.team)

        # Apply experiment filter
        if experiment_ids:
            experiments = experiments.filter(id__in=experiment_ids)
            sessions = sessions.filter(experiment_id__in=experiment_ids)
            messages = messages.filter(chat__experiment_session__experiment_id__in=experiment_ids)
            participants = participants.filter(experimentsession__experiment_id__in=experiment_ids).distinct()

        # Apply platform filter
        if platform_names:
            sessions = sessions.filter(experiment_channel__platform__in=platform_names)
            messages = messages.filter(chat__experiment_session__experiment_channel__platform__in=platform_names)

        return {
            "experiments": experiments,
            "sessions": sessions,
            "messages": messages,
            "participants": participants,
            "start_date": start_date,
            "end_date": end_date,
        }

    def get_active_participants_data(self, granularity: str = "daily", **filters) -> list[dict[str, Any]]:
        """Get active participants chart data"""
        cache_key = f"active_participants_{granularity}_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)
        messages = querysets["messages"].filter(message_type=ChatMessageType.HUMAN)

        trunc_func = self._get_trunc_function(granularity)

        # Group by time period and count unique participants
        participant_stats = (
            messages.annotate(period=trunc_func("created_at"))
            .values("period")
            .annotate(active_participants=Count("chat__experiment_session__participant", distinct=True))
            .order_by("period")
        )

        data = [
            {
                "date": self._format_period(stat["period"]),
                "active_participants": stat["active_participants"],
            }
            for stat in participant_stats
        ]

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_session_analytics_data(self, granularity: str = "daily", **filters) -> dict[str, list[dict[str, Any]]]:
        """Get session analytics data (total sessions and unique participants)"""
        cache_key = f"session_analytics_{granularity}_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)
        sessions = querysets["sessions"]

        trunc_func = self._get_trunc_function(granularity)

        session_stats = (
            sessions.annotate(period=trunc_func("chat__messages__created_at"))
            .values("period")
            .annotate(total_sessions=Count("id"), unique_participants=Count("participant", distinct=True))
            .order_by("period")
        )

        data = {"sessions": [], "participants": []}

        for stat in session_stats:
            period_str = self._format_period(stat["period"])
            data["sessions"].append({"date": period_str, "active_sessions": stat["total_sessions"]})
            data["participants"].append({"date": period_str, "active_participants": stat["unique_participants"]})

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_message_volume_data(self, granularity: str = "daily", **filters) -> dict[str, list[dict[str, Any]]]:
        """Get message volume trends (participant vs bot messages)"""
        cache_key = f"message_volume_{granularity}_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)
        messages = querysets["messages"]

        trunc_func = self._get_trunc_function(granularity)

        message_stats = (
            messages.annotate(period=trunc_func("created_at"))
            .values("period")
            .annotate(
                human_messages=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)),
                ai_messages=Count("id", filter=Q(message_type=ChatMessageType.AI)),
                total_messages=Count("id"),
            )
            .order_by("period")
        )

        data = {"human_messages": [], "ai_messages": [], "totals": []}

        for stat in message_stats:
            period_str = self._format_period(stat["period"])
            data["human_messages"].append({"date": period_str, "count": stat["human_messages"]})
            data["ai_messages"].append({"date": period_str, "count": stat["ai_messages"]})
            data["totals"].append(
                {
                    "date": period_str,
                    "human_messages": stat["human_messages"],
                    "ai_messages": stat["ai_messages"],
                    "total_messages": stat["total_messages"],
                }
            )

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_bot_performance_summary(
        self, page: int = 1, page_size: int = 10, order_by: str = "messages", order_dir: str = "desc", **filters
    ) -> dict[str, Any]:
        """Get bot performance summary with rankings, pagination, and ordering"""

        # Extract pagination/ordering from filters for cache key
        cache_filters = {k: v for k, v in filters.items() if k not in ["page", "page_size", "order_by", "order_dir"]}
        cache_key = f"bot_performance_{hash(str(sorted(cache_filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)

        if not cached_data:
            querysets = self.get_filtered_queryset_base(**cache_filters)
            experiments = querysets["experiments"]

            # Get performance metrics for each experiment
            performance_data = []
            for experiment in experiments:
                exp_sessions = querysets["sessions"].filter(experiment=experiment)
                exp_messages = querysets["messages"].filter(chat__experiment_session__experiment=experiment)

                participants_count = exp_sessions.values("participant").distinct().count()
                sessions_count = exp_sessions.count()
                messages_count = exp_messages.count()

                # Calculate average session duration
                completed_sessions = exp_sessions.filter(ended_at__isnull=False)
                avg_duration = None
                if completed_sessions.exists():
                    durations = []
                    for session in completed_sessions:
                        if session.ended_at and session.created_at:
                            duration = session.ended_at - session.created_at
                            durations.append(duration.total_seconds() / 60)
                    avg_duration = sum(durations) / len(durations) if durations else 0

                # Completion rate
                completion_rate = (completed_sessions.count() / sessions_count) if sessions_count > 0 else 0

                experiment_url = reverse(
                    "chatbots:single_chatbot_home", kwargs={"team_slug": self.team.slug, "experiment_id": experiment.id}
                )
                performance_data.append(
                    {
                        "experiment_id": experiment.id,
                        "experiment_name": experiment.name,
                        "experiment_url": experiment_url,
                        "participants": participants_count,
                        "sessions": sessions_count,
                        "messages": messages_count,
                        "avg_session_duration": avg_duration,
                        "completion_rate": completion_rate,
                        "avg_messages_per_session": messages_count / sessions_count if sessions_count > 0 else 0,
                    }
                )

            DashboardCache.set_cached_data(self.team, cache_key, performance_data)
            cached_data = performance_data

        # Apply ordering
        reverse_order = order_dir.lower() == "desc"
        if order_by in self.VALID_ORDER_FIELDS:
            cached_data.sort(key=lambda x: x[order_by] or 0, reverse=reverse_order)
        else:
            # Default to messages if invalid order_by
            cached_data.sort(key=lambda x: x["messages"], reverse=True)

        # Apply pagination
        total_count = len(cached_data)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_data = cached_data[start_idx:end_idx]

        return {
            "results": paginated_data,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size,
            "has_next": end_idx < total_count,
            "has_previous": page > 1,
            "order_by": order_by,
            "order_dir": order_dir,
        }

    def get_user_engagement_data(self, limit: int = 10, **filters) -> dict[str, Any]:
        """Get user engagement analysis data"""
        cache_key = f"user_engagement_{limit}_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)
        participants = querysets["participants"]

        # Get participant engagement stats
        date_filter = Q(experimentsession__chat__messages__created_at__gte=querysets["start_date"]) & Q(
            experimentsession__chat__messages__created_at__lte=querysets["end_date"]
        )

        participant_stats = (
            participants.annotate(
                total_messages=Count(
                    "experimentsession__chat__messages",
                    filter=Q(experimentsession__chat__messages__message_type=ChatMessageType.HUMAN) & date_filter,
                ),
                total_sessions=Count("experimentsession", filter=date_filter, distinct=True),
                last_activity=Max("experimentsession__chat__messages__created_at"),
            )
            .filter(total_messages__gt=0)
            .order_by("-total_messages")
        )

        # Most active participants
        most_active = [self._format_participant_data(p) for p in participant_stats[:limit]]

        # Session length distribution
        sessions = querysets["sessions"].filter(ended_at__isnull=False)
        session_lengths = []
        for session in sessions:
            if session.ended_at and session.created_at:
                duration = session.ended_at - session.created_at
                session_lengths.append(duration.total_seconds() / 60)  # in minutes

        # Create histogram bins
        session_length_distribution = self._create_histogram(session_lengths, bins=10)

        data = {
            "most_active_participants": most_active,
            "session_length_distribution": session_length_distribution,
        }

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_channel_breakdown_data(self, **filters) -> dict[str, Any]:
        """Get channel breakdown statistics by platform"""
        cache_key = f"channel_breakdown_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)

        # Get platform statistics by aggregating all channels per platform

        from apps.channels.models import ChannelPlatform

        platform_data = []

        # Get all platforms that have channels in this team
        platforms_in_use = (
            ExperimentChannel.objects.filter(team=self.team, deleted=False)
            .order_by()
            .values_list("platform", flat=True)
            .distinct()
        )
        for platform in platforms_in_use:
            platform_sessions = querysets["sessions"].filter(experiment_channel__platform=platform)
            platform_messages = querysets["messages"].filter(
                chat__experiment_session__experiment_channel__platform=platform
            )
            platform_participants = platform_sessions.values("participant").distinct().count()

            # Get human-readable platform name
            platform_label = dict(ChannelPlatform.choices).get(platform, platform.title())

            platform_data.append(
                {
                    "platform": platform,
                    "platform_name": platform_label,
                    "sessions": platform_sessions.count(),
                    "messages": platform_messages.count(),
                    "participants": platform_participants,
                    "human_messages": platform_messages.filter(message_type=ChatMessageType.HUMAN).count(),
                    "ai_messages": platform_messages.filter(message_type=ChatMessageType.AI).count(),
                }
            )

        # Calculate totals and percentages
        total_sessions = sum(item["sessions"] for item in platform_data)
        total_messages = sum(item["messages"] for item in platform_data)
        total_participants = sum(item["participants"] for item in platform_data)

        for item in platform_data:
            item["session_percentage"] = (item["sessions"] / total_sessions * 100) if total_sessions > 0 else 0
            item["message_percentage"] = (item["messages"] / total_messages * 100) if total_messages > 0 else 0
            item["participant_percentage"] = (
                (item["participants"] / total_participants * 100) if total_participants > 0 else 0
            )

        data = {
            "platforms": platform_data,
            "totals": {"sessions": total_sessions, "messages": total_messages, "participants": total_participants},
        }

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_tag_analytics_data(self, **filters) -> dict[str, Any]:
        """Get tag analytics data"""
        cache_key = f"tag_analytics_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)

        # Get tags used in messages within the date range
        from django.contrib.contenttypes.models import ContentType

        from apps.annotations.models import CustomTaggedItem

        message_ct = ContentType.objects.get_for_model(ChatMessage)

        # Get tagged messages
        tagged_messages = (
            CustomTaggedItem.objects.exclude(tag__category=TagCategories.EXPERIMENT_VERSION)
            .filter(content_type=message_ct, object_id__in=querysets["messages"].values_list("id", flat=True))
            .select_related("tag")
        )

        # Count tags by category
        tag_stats = {}
        for tagged_item in tagged_messages:
            tag = tagged_item.tag
            category = str(tag.label)

            if category not in tag_stats:
                tag_stats[category] = {}

            if tag.name not in tag_stats[category]:
                tag_stats[category][tag.name] = 0

            tag_stats[category][tag.name] += 1

        data = {"tag_categories": tag_stats, "total_tagged_messages": tagged_messages.count()}

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_average_response_time_data(self, granularity: str = "daily", **filters) -> list[dict[str, Any]]:
        """Calculate average response time per period based on Trace table"""
        cache_key = f"average_response_time_{granularity}_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)
        sessions = querysets["sessions"]

        trunc_func = self._get_trunc_function(granularity)

        avg_response_stats = (
            Trace.objects.filter(session__in=sessions)
            .annotate(period=trunc_func("timestamp"))
            .values("period")
            .annotate(avg_duration_ms=Avg("duration"))
            .order_by("period")
        )

        data = []

        for stat in avg_response_stats:
            period_str = self._format_period(stat["period"])
            avg_sec = stat["avg_duration_ms"] / 1000 if stat["avg_duration_ms"] else 0
            data.append({"date": period_str, "avg_response_time_sec": round(avg_sec, 2)})

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def _create_histogram(self, data: list[float], bins: int = 10) -> list[dict[str, Any]]:
        """Create histogram data from a list of values"""
        if not data:
            return []

        min_val = min(data)
        max_val = max(data)
        bin_width = (max_val - min_val) / bins

        histogram = []
        for i in range(bins):
            bin_start = min_val + i * bin_width
            bin_end = bin_start + bin_width

            count = sum(1 for value in data if bin_start <= value < bin_end)
            if i == bins - 1:  # Include max value in last bin
                count = sum(1 for value in data if bin_start <= value <= bin_end)

            histogram.append(
                {
                    "bin_start": round(bin_start, 2),
                    "bin_end": round(bin_end, 2),
                    "count": count,
                    "label": f"{round(bin_start, 1)}-{round(bin_end, 1)} min",
                }
            )

        return histogram

    def _get_trunc_function(self, granularity: str):
        """Get the appropriate truncation function for the given granularity"""
        return self.GRANULARITY_TRUNC_MAP.get(granularity, TruncDate)

    def _format_period(self, period):
        """Format a period object to ISO string"""
        return period.isoformat() if hasattr(period, "isoformat") else str(period)

    def _format_participant_data(self, participant) -> dict[str, Any]:
        """Format participant data for engagement analysis"""
        participant_url = reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": self.team.slug, "participant_id": participant.id},
        )
        return {
            "participant_id": participant.id,
            "participant_name": participant.name or participant.identifier,
            "participant_url": participant_url,
            "total_messages": participant.total_messages,
            "total_sessions": participant.total_sessions,
            "last_activity": participant.last_activity.isoformat() if participant.last_activity else None,
        }

    def get_overview_stats(self, **filters) -> dict[str, Any]:
        """Get dashboard overview statistics"""
        cache_key = f"overview_stats_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)

        # Calculate key metrics
        stats = {
            "total_experiments": querysets["experiments"].count(),
            "total_participants": querysets["participants"].count(),
            "total_sessions": querysets["sessions"].count(),
            "total_messages": querysets["messages"].count(),
            "active_experiments": querysets["experiments"]
            .filter(sessions__in=querysets["sessions"])
            .distinct()
            .count(),
            "active_participants": querysets["sessions"].values("participant").distinct().count(),
            "completed_sessions": querysets["sessions"].filter(ended_at__isnull=False).count(),
        }

        # Calculate derived metrics
        stats["completion_rate"] = (
            stats["completed_sessions"] / stats["total_sessions"] * 100 if stats["total_sessions"] > 0 else 0
        )
        stats["avg_messages_per_session"] = (
            stats["total_messages"] / stats["total_sessions"] if stats["total_sessions"] > 0 else 0
        )
        stats["avg_sessions_per_participant"] = (
            stats["total_sessions"] / stats["active_participants"] if stats["active_participants"] > 0 else 0
        )

        DashboardCache.set_cached_data(self.team, cache_key, stats)
        return stats
