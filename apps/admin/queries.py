import csv
import hashlib
import io
from datetime import datetime

from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce, Length, TruncDate

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.participants.models import Participant


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


def _write_data_to_csv(headers, rows):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    return csv_in_memory.getvalue()
