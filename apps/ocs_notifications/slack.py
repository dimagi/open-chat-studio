import logging

from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.ocs_notifications.models import NotificationChannel, NotificationEvent
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.notifications")


def build_slack_message(notification_event: NotificationEvent) -> dict:
    """Render a notification event as a Slack BlockKit message.

    Returns a ``blocks`` payload plus a plain-text ``text`` fallback (Slack's ``chat_postMessage``
    requires ``text`` when sending blocks, and surfaced it in clients that can't render blocks).
    """
    links = dict(notification_event.links or {})
    links["View in OCS"] = absolute_url(
        reverse("ocs_notifications:notification_event_home", args=[notification_event.event_type_id])
    )
    link_text = " · ".join(f"<{url}|{label}>" for label, url in links.items())

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":loudspeaker: {notification_event.title}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Open Chat Studio notification for *{notification_event.team.name}*\n"
                        f"{notification_event.event_type.get_level_display()} · {link_text}"
                    ),
                }
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Details*\n```{notification_event.message}```"},
        },
    ]

    lines = [f"*{notification_event.title}*", "", notification_event.message]
    lines.extend(f"- <{url}|{label}>" for label, url in links.items())
    return {"blocks": blocks, "text": "\n".join(lines)}


def send_slack_notification(notification_channel: NotificationChannel, notification_event: NotificationEvent) -> bool:
    """Post a notification to the channel's Slack workspace.

    Returns True on success and False when delivery fails, so callers can fail gracefully
    without raising into the notification pipeline.
    """
    try:
        service = notification_channel.messaging_provider.get_messaging_service()
        message = build_slack_message(notification_event)
        service.send_text_message(
            message=message["text"],
            from_=notification_event.team.slug,
            to=notification_channel.channel_id or notification_channel.channel_name,
            platform=ChannelPlatform.SLACK,
            blocks=message["blocks"],
        )
        return True
    except Exception:
        logger.exception("Failed to send Slack notification to channel %s", notification_channel.channel_name)
        return False
