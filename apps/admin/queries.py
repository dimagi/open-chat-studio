import csv
import hashlib
import io
from collections import defaultdict
from datetime import datetime

from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce, Length, TruncDate

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession, Participant
from apps.teams.models import Team


def get_message_stats(start: datetime, end: datetime):
    data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )
    return data


def get_participant_stats(start: datetime, end: datetime):
    data = (
        Participant.objects.filter(created_at__gte=start, created_at__lt=end)
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )
    return data


def usage_to_csv(start: datetime, end: datetime):
    return _write_data_to_csv(["Team", "Message Count", "Total Characters"], get_usage_data(start, end))


def get_usage_data(start: datetime, end: datetime):
    """Usage approximation based on character counts."""
    usage_data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("chat__team__name")
        .annotate(
            msg_count=Count("id"),
            total_chars=Sum(
                Length("content")
                + Length("chat__experiment_session__experiment__prompt_text")
                + Length(Coalesce("chat__experiment_session__experiment__source_material__material", Value("")))
            ),
        )
        .order_by("-msg_count")
    )
    for data in usage_data:
        yield data["chat__team__name"], data["msg_count"], data["total_chars"]


def get_whatsapp_numbers():
    return _write_data_to_csv(
        ["Team", "Experiment", "Messaging Provider", "Account", "Number"], get_whatsapp_number_data()
    )


def get_whatsapp_number_data():
    channels = ExperimentChannel.objects.filter(deleted=False, platform=ChannelPlatform.WHATSAPP).values(
        "extra_data",
        "team__name",
        "experiment__name",
        "messaging_provider__name",
        "messaging_provider__type",
        "messaging_provider__config",
    )
    for channel in channels:
        account = "---"
        provider_type = channel["messaging_provider__type"]
        match provider_type:
            case "twilio":
                account = channel["messaging_provider__config"].get("account_sid", "---")
            case "turn":
                token = channel["messaging_provider__config"].get("auth_token")
                if token:
                    account = hashlib.shake_128(token.encode()).hexdigest(8)

        provider_name = channel["messaging_provider__name"]
        if provider_name:
            provider_name = f"{provider_name} ({provider_type})"
        else:
            provider_name = "---"

        yield (
            channel["team__name"],
            channel["experiment__name"],
            provider_name,
            account,
            channel["extra_data"].get("number", "---"),
        )


def get_whatsapp_message_stats(start: datetime, end: datetime):
    rows = (
        ChatMessage.objects.filter(
            created_at__gte=start,
            created_at__lt=end,
            chat__experiment_session__experiment_channel__platform=ChannelPlatform.WHATSAPP,
            message_type__in=["human", "ai"],
        )
        .values(
            "chat__team__name",
            "chat__experiment_session__experiment__name",
            "chat__experiment_session__experiment_channel__extra_data",
            "message_type",
        )
        .annotate(count=Count("id"))
        .order_by("chat__team__name", "chat__experiment_session__experiment__name")
    )

    # Pivot into (team, experiment, number, human_count, ai_count)
    pivot = defaultdict(lambda: {"human": 0, "ai": 0})
    for row in rows:
        team = row["chat__team__name"]
        experiment = row["chat__experiment_session__experiment__name"]
        extra_data = row["chat__experiment_session__experiment_channel__extra_data"] or {}
        number = extra_data.get("number", "---")
        key = (team, experiment, number)
        pivot[key][row["message_type"]] = row["count"]

    return [
        {
            "team": team,
            "experiment": experiment,
            "number": number,
            "human_count": counts["human"],
            "ai_count": counts["ai"],
        }
        for (team, experiment, number), counts in sorted(pivot.items())
    ]


def get_top_teams(start: datetime, end: datetime):
    msg_data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("chat__team_id", "chat__team__name")
        .annotate(
            msg_count=Count("id"),
            session_count=Count("chat__experiment_session", distinct=True),
        )
        .order_by("-msg_count")
    )

    participant_data = (
        ExperimentSession.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("team_id")
        .annotate(participant_count=Count("participant", distinct=True))
    )
    participant_map = {row["team_id"]: row["participant_count"] for row in participant_data}

    return [
        {
            "team": row["chat__team__name"],
            "msg_count": row["msg_count"],
            "session_count": row["session_count"],
            "participant_count": participant_map.get(row["chat__team_id"], 0),
        }
        for row in msg_data
    ]


def get_platform_breakdown(start: datetime, end: datetime):
    rows = (
        ExperimentSession.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(Q(platform=ChannelPlatform.EVALUATIONS) | Q(platform__isnull=True) | Q(platform=""))
        .values("platform")
        .annotate(
            session_count=Count("id"),
            msg_count=Count(
                "chat__messages",
                filter=Q(chat__messages__created_at__gte=start, chat__messages__created_at__lt=end),
            ),
        )
        .order_by("-session_count")
    )
    result = []
    for row in rows:
        try:
            label = ChannelPlatform(row["platform"]).label
        except ValueError:
            label = row["platform"]
        result.append(
            {
                "platform": label,
                "session_count": row["session_count"],
                "msg_count": row["msg_count"],
            }
        )
    return result


def get_team_activity_summary(start: datetime, end: datetime):
    active_team_ids = set(
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .values_list("chat__team_id", flat=True)
        .distinct()
    )
    all_teams = list(Team.objects.values_list("id", "name"))
    inactive_teams = sorted(name for tid, name in all_teams if tid not in active_team_ids)
    return {
        "active_count": len(active_team_ids),
        "total_count": len(all_teams),
        "inactive_teams": inactive_teams,
    }


def get_period_totals(start: datetime, end: datetime):
    return {
        "messages": ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end).count(),
        "participants": Participant.objects.filter(created_at__gte=start, created_at__lt=end).count(),
        "sessions": ExperimentSession.objects.filter(created_at__gte=start, created_at__lt=end).count(),
    }


def get_top_experiments(start: datetime, end: datetime, limit: int = 10):
    rows = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .values(
            "chat__experiment_session__experiment__name",
            "chat__experiment_session__experiment__team__name",
        )
        .annotate(
            msg_count=Count("id"),
            session_count=Count("chat__experiment_session", distinct=True),
        )
        .order_by("-msg_count")[:limit]
    )
    return [
        {
            "team": row["chat__experiment_session__experiment__team__name"],
            "experiment": row["chat__experiment_session__experiment__name"],
            "msg_count": row["msg_count"],
            "session_count": row["session_count"],
        }
        for row in rows
    ]


def top_teams_to_csv(start: datetime, end: datetime):
    data = get_top_teams(start, end)
    rows = ((d["team"], d["msg_count"], d["session_count"], d["participant_count"]) for d in data)
    return _write_data_to_csv(["Team", "Messages", "Sessions", "Participants"], rows)


def top_experiments_to_csv(start: datetime, end: datetime):
    data = get_top_experiments(start, end)
    rows = ((d["team"], d["experiment"], d["msg_count"], d["session_count"]) for d in data)
    return _write_data_to_csv(["Team", "Experiment", "Messages", "Sessions"], rows)


def whatsapp_message_stats_to_csv(start: datetime, end: datetime):
    stats = get_whatsapp_message_stats(start, end)
    rows = ((s["team"], s["experiment"], s["number"], s["human_count"], s["ai_count"]) for s in stats)
    return _write_data_to_csv(["Team", "Experiment", "Channel Number", "Human Messages", "AI Messages"], rows)


def _write_data_to_csv(headers, rows):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    return csv_in_memory.getvalue()
