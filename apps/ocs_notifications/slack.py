import logging

from apps.channels.models import ChannelPlatform
from apps.ocs_notifications.models import NotificationChannel, NotificationEvent

logger = logging.getLogger("ocs.notifications")


def build_slack_message(notification_event: NotificationEvent) -> str:
    """Render a notification event as a Slack-formatted message."""
    lines = [
        f"*{notification_event.title}*",
        "",
        notification_event.message,
        "",
        f"_Level: {notification_event.event_type.get_level_display()} · Team: {notification_event.team.name}_",
    ]
    if notification_event.links:
        lines.append("")
        lines.append("Links:")
        lines.extend(f"- <{url}|{label}>" for label, url in notification_event.links.items())
    return "\n".join(lines)


def _resolve_channel_id(service, channel_name: str) -> str:
    """Resolve a channel name to its ID, falling back to the raw name.

    The Slack API accepts a public channel name directly, so the raw name is a safe fallback
    when the bot cannot enumerate channels (e.g. the channel is private or the bot has limited
    scopes).
    """
    channel = service.get_channel_by_name(channel_name)
    return channel["id"] if channel else channel_name


def send_slack_notification(notification_channel: NotificationChannel, notification_event: NotificationEvent) -> bool:
    """Post a notification to the channel's Slack workspace.

    Returns True on success and False when delivery fails, so callers can fail gracefully
    without raising into the notification pipeline.
    """
    try:
        service = notification_channel.messaging_provider.get_messaging_service()
        service.send_text_message(
            message=build_slack_message(notification_event),
            from_=notification_event.team.slug,
            to=_resolve_channel_id(service, notification_channel.channel_name),
            platform=ChannelPlatform.SLACK,
        )
        return True
    except Exception:
        logger.exception("Failed to send Slack notification to channel %s", notification_channel.channel_name)
        return False
