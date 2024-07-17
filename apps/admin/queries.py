import csv
import io
from datetime import datetime

from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce, Length, TruncDate

from apps.chat.models import ChatMessage
from apps.experiments.models import Participant


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
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Team", "Message Count", "Total Characters"])
    for data in get_usage_data(start, end):
        writer.writerow(data)

    return csv_in_memory.getvalue()


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
