import csv
import hashlib
import io
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.cost_tracking.models import UsageRecord
from apps.experiments.models import ExperimentSession, Participant
from apps.teams.metadata import get_team_metadata_fields
from apps.teams.models import Flag, Team
from apps.trace.models import Trace, TraceStatus

COST_TRACKING_FLAG = "flag_ai_cost_monitoring"

_ZERO = Decimal(0)
_COST_FIELD = DecimalField(max_digits=14, decimal_places=8)
_QUANTITY_FIELD = DecimalField(max_digits=18, decimal_places=4)


def get_message_stats(start: datetime, end: datetime):
    data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )
    return data


def get_participant_stats(start: datetime, end: datetime):
    data = (
        Participant.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(platform=ChannelPlatform.EVALUATIONS)
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )
    return data


def usage_to_csv(start: datetime, end: datetime):
    metadata_fields = get_team_metadata_fields()
    headers = ["Team", "Run Count", "Total Tokens"] + [field["label"] for field in metadata_fields]
    rows = (
        (team_name, run_count, total_tokens, *(metadata.get(field["key"], "") for field in metadata_fields))
        for team_name, run_count, total_tokens, metadata in get_usage_data(start, end)
    )
    return _write_data_to_csv(headers, rows)


def get_usage_data(start: datetime, end: datetime):
    """Per-team usage from completed trace token counts.

    Only includes traces with a settled status (excludes PENDING) and excludes the
    evaluations platform. Pre-tracing periods will report lower totals than the
    legacy character-based proxy.
    """
    usage_data = (
        Trace.objects.filter(timestamp__gte=start, timestamp__lt=end)
        .exclude(status=TraceStatus.PENDING)
        .exclude(session__platform=ChannelPlatform.EVALUATIONS)
        .values("team_id", "team__name", "team__metadata")
        .annotate(
            run_count=Count("id"),
            total_tokens=Coalesce(Sum("n_total_tokens"), Value(0)),
        )
        .order_by("-run_count", "team__name")
    )
    for data in usage_data:
        yield data["team__name"], data["run_count"], data["total_tokens"], data["team__metadata"] or {}


def get_token_usage_by_team(start: datetime, end: datetime):
    """Per-team run count + total tokens from settled, non-eval traces."""
    return (
        Trace.objects.filter(timestamp__gte=start, timestamp__lt=end)
        .exclude(status=TraceStatus.PENDING)
        .exclude(session__platform=ChannelPlatform.EVALUATIONS)
        .values("team_id", "team__name")
        .annotate(
            run_count=Count("id"),
            total_tokens=Coalesce(Sum("n_total_tokens"), Value(0)),
        )
        .order_by("-run_count", "team__name")
    )


def get_cost_usage_by_team(start: datetime, end: datetime):
    """Per (team, provider, model, currency) cost + tokens from UsageRecord.

    Only teams with `flag_ai_cost_monitoring` enabled record UsageRecords, so
    this covers a subset of the teams returned by `get_token_usage_by_team`.
    """
    return (
        UsageRecord.objects.filter(timestamp__gte=start, timestamp__lt=end)
        .values("team_id", "provider_type", "model_name", "currency")
        .annotate(
            cost=Coalesce(Sum("cost"), _ZERO, output_field=_COST_FIELD),
            tokens=Coalesce(Sum("quantity"), _ZERO, output_field=_QUANTITY_FIELD),
        )
        .order_by("team_id", "-cost")
    )


def get_cost_tracking_team_ids() -> set[int]:
    """Team ids for which `flag_ai_cost_monitoring` is active."""
    flag = Flag.objects.filter(name=COST_TRACKING_FLAG).prefetch_related("teams").first()
    if flag is None or flag.everyone is False:
        return set()
    if flag.everyone is True:
        return set(Team.objects.values_list("id", flat=True))
    return set(flag.teams.values_list("id", flat=True))


def build_usage_report(start: datetime, end: datetime) -> dict:
    """Cross-team usage: token totals for every team (always populated) merged
    with per-model cost detail where cost tracking is on. `cost_tracking_enabled`
    flags which teams' cost detail is complete vs. token-only.

    `total_cost` is a `{currency: amount}` map, not a scalar: a team can have
    records in more than one currency and summing them would be meaningless.
    """
    token_rows = list(get_token_usage_by_team(start, end))
    cost_rows = list(get_cost_usage_by_team(start, end))
    enabled_team_ids = get_cost_tracking_team_ids()

    team_names = {row["team_id"]: row["team__name"] for row in token_rows}
    missing_ids = {row["team_id"] for row in cost_rows} - team_names.keys()
    if missing_ids:
        team_names.update(dict(Team.objects.filter(id__in=missing_ids).values_list("id", "name")))

    teams: dict[int, dict] = {}

    def _entry(team_id: int) -> dict:
        if team_id not in teams:
            teams[team_id] = {
                "team_id": team_id,
                "team_name": team_names.get(team_id),
                "run_count": 0,
                "total_tokens": 0,
                "cost_tracking_enabled": team_id in enabled_team_ids,
                "total_cost": defaultdict(Decimal),  # currency -> amount
                "models": [],
            }
        return teams[team_id]

    for row in token_rows:
        entry = _entry(row["team_id"])
        entry["run_count"] = row["run_count"]
        entry["total_tokens"] = row["total_tokens"]

    for row in cost_rows:
        entry = _entry(row["team_id"])
        entry["total_cost"][row["currency"]] += row["cost"]
        entry["models"].append(
            {
                "provider_type": row["provider_type"],
                "model_name": row["model_name"],
                "currency": row["currency"],
                "tokens": int(row["tokens"] or 0),
                "cost": str(row["cost"]),
            }
        )

    result = sorted(teams.values(), key=lambda t: (-t["run_count"], t["team_name"] or ""))
    for entry in result:
        entry["total_cost"] = {currency: str(amount) for currency, amount in entry["total_cost"].items()}

    return {"start": start.isoformat(), "end": end.isoformat(), "teams": result}


def get_whatsapp_numbers():
    return _write_data_to_csv(
        ["Team", "Chatbot", "Messaging Provider", "Account", "Number", "Channel Active"], get_whatsapp_number_data()
    )


def get_whatsapp_number_data():
    # Use unfiltered queryset so deleted channels are included; the default manager
    # hides them, which would make the "Channel Active" column always True.
    channels = (
        ExperimentChannel.objects.get_unfiltered_queryset()
        .filter(platform=ChannelPlatform.WHATSAPP)
        .values(
            "deleted",
            "extra_data",
            "team__name",
            "experiment__name",
            "messaging_provider__name",
            "messaging_provider__type",
            "messaging_provider__config",
        )
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
            not channel["deleted"],
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
            "chat__team__slug",
            "chat__experiment_session__experiment__id",
            "chat__experiment_session__experiment__name",
            "chat__experiment_session__experiment_channel__extra_data",
            "chat__experiment_session__experiment_channel__deleted",
            "message_type",
        )
        .annotate(count=Count("id"))
        .order_by("chat__team__name", "chat__experiment_session__experiment__name")
    )

    # Pivot into (team, experiment, number, human_count, ai_count)
    pivot = defaultdict(lambda: {"human": 0, "ai": 0})
    key_metadata = {}
    for row in rows:
        team_slug = row["chat__team__slug"]
        experiment_id = row["chat__experiment_session__experiment__id"]
        extra_data = row["chat__experiment_session__experiment_channel__extra_data"] or {}
        deleted = row["chat__experiment_session__experiment_channel__deleted"]
        number = extra_data.get("number", "---")
        key = (team_slug, experiment_id, number)
        pivot[key][row["message_type"]] += row["count"]
        # If a channel was deleted and recreated for the same (team, experiment, number),
        # the query returns separate rows per deleted status. Treat the pivot as active
        # if any underlying channel is active.
        existing = key_metadata.get(key)
        key_metadata[key] = {
            "team_name": row["chat__team__name"],
            "experiment_name": row["chat__experiment_session__experiment__name"],
            "channel_active": (existing["channel_active"] if existing else False) or not deleted,
        }

    results = [
        {
            "team": key_metadata[key]["team_name"],
            "team_slug": team_slug,
            "experiment": key_metadata[key]["experiment_name"],
            "experiment_id": experiment_id,
            "channel_active": key_metadata[key]["channel_active"],
            "number": number,
            "human_count": counts["human"],
            "ai_count": counts["ai"],
        }
        for key, counts in pivot.items()
        for (team_slug, experiment_id, number) in [key]
    ]
    results.sort(key=lambda r: r["human_count"], reverse=True)
    return results


def get_top_teams(start: datetime, end: datetime, limit: int = 10):
    msg_data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .values("chat__team_id", "chat__team__name", "chat__team__slug", "chat__team__metadata")
        .annotate(
            msg_count=Count("id"),
            session_count=Count("chat__experiment_session", distinct=True),
        )
        .order_by("-msg_count")[:limit]
    )

    participant_data = (
        ExperimentSession.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(platform=ChannelPlatform.EVALUATIONS)
        .values("team_id")
        .annotate(participant_count=Count("participant", distinct=True))
    )
    participant_map = {row["team_id"]: row["participant_count"] for row in participant_data}

    return [
        {
            "team": row["chat__team__name"],
            "team_slug": row["chat__team__slug"],
            "metadata": row["chat__team__metadata"] or {},
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
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .values_list("chat__team_id", flat=True)
        .distinct()
    )
    all_teams = list(Team.objects.values_list("id", "name", "slug"))
    inactive_teams = sorted(
        ({"name": name, "slug": slug} for tid, name, slug in all_teams if tid not in active_team_ids),
        key=lambda t: t["name"],
    )
    return {
        "active_count": len(active_team_ids),
        "total_count": len(all_teams),
        "inactive_teams": inactive_teams,
    }


def get_period_totals(start: datetime, end: datetime):
    return {
        "messages": (
            ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
            .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
            .count()
        ),
        "participants": (
            Participant.objects.filter(created_at__gte=start, created_at__lt=end)
            .exclude(platform=ChannelPlatform.EVALUATIONS)
            .count()
        ),
        "sessions": (
            ExperimentSession.objects.filter(created_at__gte=start, created_at__lt=end)
            .exclude(platform=ChannelPlatform.EVALUATIONS)
            .count()
        ),
    }


def get_top_experiments(start: datetime, end: datetime, limit: int = 10):
    rows = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .values(
            "chat__experiment_session__experiment__id",
            "chat__experiment_session__experiment__name",
            "chat__experiment_session__experiment__team__name",
            "chat__experiment_session__experiment__team__slug",
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
            "team_slug": row["chat__experiment_session__experiment__team__slug"],
            "experiment": row["chat__experiment_session__experiment__name"],
            "experiment_id": row["chat__experiment_session__experiment__id"],
            "msg_count": row["msg_count"],
            "session_count": row["session_count"],
        }
        for row in rows
    ]


def top_teams_to_csv(start: datetime, end: datetime):
    metadata_fields = get_team_metadata_fields()
    data = get_top_teams(start, end)
    headers = ["Team", "Messages", "Sessions", "Participants"] + [field["label"] for field in metadata_fields]
    rows = (
        (
            d["team"],
            d["msg_count"],
            d["session_count"],
            d["participant_count"],
            *(d["metadata"].get(field["key"], "") for field in metadata_fields),
        )
        for d in data
    )
    return _write_data_to_csv(headers, rows)


def get_all_teams_metadata():
    metadata_fields = get_team_metadata_fields()
    teams = Team.objects.order_by("name").values("name", "slug", "metadata")
    for team in teams:
        metadata = team["metadata"] or {}
        yield (team["name"], team["slug"], *(metadata.get(field["key"], "") for field in metadata_fields))


def team_metadata_to_csv():
    metadata_fields = get_team_metadata_fields()
    headers = ["Team", "Slug"] + [field["label"] for field in metadata_fields]
    return _write_data_to_csv(headers, get_all_teams_metadata())


def top_experiments_to_csv(start: datetime, end: datetime):
    data = get_top_experiments(start, end)
    rows = ((d["team"], d["experiment"], d["msg_count"], d["session_count"]) for d in data)
    return _write_data_to_csv(["Team", "Chatbot", "Messages", "Sessions"], rows)


def whatsapp_message_stats_to_csv(start: datetime, end: datetime):
    stats = get_whatsapp_message_stats(start, end)
    rows = (
        (s["team"], s["experiment"], s["number"], s["human_count"], s["ai_count"], s["channel_active"]) for s in stats
    )
    return _write_data_to_csv(
        ["Team", "Chatbot", "Channel Number", "Human Messages", "AI Messages", "Channel Active"], rows
    )


def _write_data_to_csv(headers, rows):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    return csv_in_memory.getvalue()
