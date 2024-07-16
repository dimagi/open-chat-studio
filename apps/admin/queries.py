from datetime import datetime

from django.db.models import Count
from django.db.models.functions import TruncDate

from apps.chat.models import ChatMessage


def get_message_stats(start: datetime, end: datetime):
    data = (
        ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    )
    return data
