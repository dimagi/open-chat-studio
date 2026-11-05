from django import template

from apps.experiments.const import LEGACY_WEB_CHAT_SUCCESSOR_URL, LEGACY_WEB_CHAT_SUNSET_AT

register = template.Library()


@register.inclusion_tag("experiments/components/legacy_web_chat_warning.html")
def legacy_web_chat_warning():
    return {"sunset_at": LEGACY_WEB_CHAT_SUNSET_AT, "successor_url": LEGACY_WEB_CHAT_SUCCESSOR_URL}
