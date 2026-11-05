from django import template

from apps.chat.models import ChatMessage, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.moderation.services import strike_categories

register = template.Library()


@register.filter
def flagged_strike_categories(detail: ModerationDetail | None) -> list[str]:
    """The strike categories the provider flagged on a message, sorted by name."""
    if detail is None or detail.output is None:
        return []
    return sorted(strike_categories(detail.output))


@register.filter
def is_flagged(message: ChatMessage) -> bool:
    """Whether the moderation provider flagged the message."""
    return message.moderation_status == ModerationStatus.FLAGGED
