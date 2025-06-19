from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate, TruncHour, TruncMonth, TruncWeek
from django.utils import timezone

from apps.channels.models import ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, Participant

from .models import DashboardCache


class DashboardService:
    """Service class for dashboard analytics operations"""

    def __init__(self, team):
        self.team = team

    def get_filtered_queryset_base(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        experiment_ids: list[int] | None = None,
        channel_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Get base querysets with common filters applied"""

        # Default date range (last 30 days)
        if not end_date:
            end_date = timezone.now()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        base_filters = {"created_at__gte": start_date, "created_at__lte": end_date}

        # Base querysets
        experiments = Experiment.objects.filter(team=self.team, is_archived=False)
        sessions = ExperimentSession.objects.filter(team=self.team, **base_filters)
        messages = ChatMessage.objects.filter(chat__team=self.team, **base_filters)
        participants = Participant.objects.filter(team=self.team)

        # Apply experiment filter
        if experiment_ids:
            experiments = experiments.filter(id__in=experiment_ids)
            sessions = sessions.filter(experiment_id__in=experiment_ids)
            messages = messages.filter(chat__experiment_session__experiment_id__in=experiment_ids)

        # Apply channel filter
        if channel_ids:
            sessions = sessions.filter(experiment_channel_id__in=channel_ids)
            messages = messages.filter(chat__experiment_session__experiment_channel_id__in=channel_ids)

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

        # Choose truncation based on granularity
        trunc_func = {"hourly": TruncHour, "daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}.get(
            granularity, TruncDate
        )

        # Group by time period and count unique participants
        participant_stats = (
            messages.annotate(period=trunc_func("created_at"))
            .values("period")
            .annotate(active_participants=Count("chat__experiment_session__participant", distinct=True))
            .order_by("period")
        )

        data = [
            {
                "date": stat["period"].isoformat() if hasattr(stat["period"], "isoformat") else str(stat["period"]),
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

        trunc_func = {"hourly": TruncHour, "daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}.get(
            granularity, TruncDate
        )

        session_stats = (
            sessions.annotate(period=trunc_func("created_at"))
            .values("period")
            .annotate(total_sessions=Count("id"), unique_participants=Count("participant", distinct=True))
            .order_by("period")
        )

        data = {"sessions": [], "participants": []}

        for stat in session_stats:
            period_str = stat["period"].isoformat() if hasattr(stat["period"], "isoformat") else str(stat["period"])
            data["sessions"].append({"date": period_str, "total_sessions": stat["total_sessions"]})
            data["participants"].append({"date": period_str, "unique_participants": stat["unique_participants"]})

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

        trunc_func = {"hourly": TruncHour, "daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}.get(
            granularity, TruncDate
        )

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
            period_str = stat["period"].isoformat() if hasattr(stat["period"], "isoformat") else str(stat["period"])
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

                performance_data.append(
                    {
                        "experiment_id": experiment.id,
                        "experiment_name": experiment.name,
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
        if order_by in [
            "participants",
            "sessions",
            "messages",
            "completion_rate",
            "avg_session_duration",
            "avg_messages_per_session",
        ]:
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
        participant_stats = (
            participants.annotate(
                total_messages=Count(
                    "experimentsession__chat__messages",
                    filter=Q(experimentsession__chat__messages__message_type=ChatMessageType.HUMAN)
                    & Q(experimentsession__chat__messages__created_at__gte=querysets["start_date"])
                    & Q(experimentsession__chat__messages__created_at__lte=querysets["end_date"]),
                ),
                total_sessions=Count(
                    "experimentsession",
                    filter=Q(experimentsession__created_at__gte=querysets["start_date"])
                    & Q(experimentsession__created_at__lte=querysets["end_date"]),
                ),
                last_activity=Max("experimentsession__chat__messages__created_at"),
                experiments_count=Count("experimentsession__experiment", distinct=True),
            )
            .filter(total_messages__gt=0)
            .order_by("-total_messages")
        )

        # Most active participants
        most_active = []
        for participant in participant_stats[:limit]:
            avg_messages_per_session = (
                participant.total_messages / participant.total_sessions if participant.total_sessions > 0 else 0
            )
            most_active.append(
                {
                    "participant_id": participant.id,
                    "participant_name": participant.name or participant.identifier,
                    "total_messages": participant.total_messages,
                    "total_sessions": participant.total_sessions,
                    "avg_messages_per_session": avg_messages_per_session,
                    "last_activity": participant.last_activity.isoformat() if participant.last_activity else None,
                    "experiments_count": participant.experiments_count,
                }
            )

        # Least active participants (but with at least some activity)
        least_active = []
        for participant in participant_stats.order_by("total_messages")[:limit]:
            avg_messages_per_session = (
                participant.total_messages / participant.total_sessions if participant.total_sessions > 0 else 0
            )
            least_active.append(
                {
                    "participant_id": participant.id,
                    "participant_name": participant.name or participant.identifier,
                    "total_messages": participant.total_messages,
                    "total_sessions": participant.total_sessions,
                    "avg_messages_per_session": avg_messages_per_session,
                    "last_activity": participant.last_activity.isoformat() if participant.last_activity else None,
                    "experiments_count": participant.experiments_count,
                }
            )

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
            "least_active_participants": least_active,
            "session_length_distribution": session_length_distribution,
            "total_participants": participant_stats.count(),
        }

        DashboardCache.set_cached_data(self.team, cache_key, data)
        return data

    def get_channel_breakdown_data(self, **filters) -> dict[str, Any]:
        """Get channel breakdown statistics"""
        cache_key = f"channel_breakdown_{hash(str(sorted(filters.items())))}"
        cached_data = DashboardCache.get_cached_data(self.team, cache_key)
        if cached_data:
            return cached_data

        querysets = self.get_filtered_queryset_base(**filters)

        # Get channel statistics
        channels = ExperimentChannel.objects.filter(team=self.team, deleted=False)
        channel_data = []

        for channel in channels:
            channel_sessions = querysets["sessions"].filter(experiment_channel=channel)
            channel_messages = querysets["messages"].filter(chat__experiment_session__experiment_channel=channel)
            channel_participants = channel_sessions.values("participant").distinct().count()

            channel_data.append(
                {
                    "channel_id": channel.id,
                    "channel_name": channel.name,
                    "platform": channel.platform,
                    "sessions": channel_sessions.count(),
                    "messages": channel_messages.count(),
                    "participants": channel_participants,
                    "human_messages": channel_messages.filter(message_type=ChatMessageType.HUMAN).count(),
                    "ai_messages": channel_messages.filter(message_type=ChatMessageType.AI).count(),
                }
            )

        # Calculate totals and percentages
        total_sessions = sum(item["sessions"] for item in channel_data)
        total_messages = sum(item["messages"] for item in channel_data)
        total_participants = sum(item["participants"] for item in channel_data)

        for item in channel_data:
            item["session_percentage"] = (item["sessions"] / total_sessions * 100) if total_sessions > 0 else 0
            item["message_percentage"] = (item["messages"] / total_messages * 100) if total_messages > 0 else 0
            item["participant_percentage"] = (
                (item["participants"] / total_participants * 100) if total_participants > 0 else 0
            )

        data = {
            "channels": channel_data,
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
        tagged_messages = CustomTaggedItem.objects.filter(
            content_type=message_ct, object_id__in=querysets["messages"].values_list("id", flat=True)
        ).select_related("tag")

        # Count tags by category
        tag_stats = {}
        for tagged_item in tagged_messages:
            tag = tagged_item.tag
            category = tag.category

            if category not in tag_stats:
                tag_stats[category] = {}

            if tag.name not in tag_stats[category]:
                tag_stats[category][tag.name] = 0

            tag_stats[category][tag.name] += 1

        data = {"tag_categories": tag_stats, "total_tagged_messages": tagged_messages.count()}

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
            "human_messages": querysets["messages"].filter(message_type=ChatMessageType.HUMAN).count(),
            "ai_messages": querysets["messages"].filter(message_type=ChatMessageType.AI).count(),
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
