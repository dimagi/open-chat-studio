import hashlib
import json
import logging
from enum import Enum

from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.ocs_notifications.models import (
    EventType,
    EventUser,
    LevelChoices,
    NotificationEvent,
    UserNotificationPreferences,
)
from apps.teams.flags import Flags
from apps.teams.models import Flag, Team
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.notifications")

CACHE_KEY_FORMAT = "{user_id}-{team_slug}-unread-notifications-count"


class DurationTimeDelta(Enum):
    DURATION_8H = timezone.timedelta(hours=8)
    DURATION_1D = timezone.timedelta(days=1)
    DURATION_1W = timezone.timedelta(weeks=1)
    DURATION_1M = timezone.timedelta(weeks=4)
    FOREVER = timezone.timedelta(weeks=10400)  # ~200 years, effectively forever


# Map duration parameter values to hours
TIMEDELTA_MAP = {
    "8h": DurationTimeDelta.DURATION_8H,
    "1d": DurationTimeDelta.DURATION_1D,
    "1w": DurationTimeDelta.DURATION_1W,
    "1m": DurationTimeDelta.DURATION_1M,
    "forever": DurationTimeDelta.FOREVER,
}


def create_notification(
    title: str,
    message: str,
    level: LevelChoices,
    team: Team,
    slug: str,
    event_data: dict | None = None,
    permissions: list[str] | None = None,
    links: dict | None = None,
):
    """
    Create a notification event and associate it with the given users.

    Args:
        title (str): The title of the notification.
        message (str): The message content of the notification.
        level (str): The level of the notification (info, warning, error).
        team (Team, optional): A team whose members will be associated with the notification.
        slug (str): A slug to identify the event type. Used with event_data for uniqueness.
        event_data (dict, optional): Additional data to store with the event type. Combined with slug for uniqueness.

    Returns:
        NotificationEvent: The created NotificationEvent instance, or None if creation failed.
    """
    if not _notifications_flag_is_active(team):
        return
    links = links or {}

    def _can_receive_notification(member):
        if not permissions:
            return True
        return member.has_perms(permissions)

    users = [
        member.user for member in team.membership_set.select_related("user").all() if _can_receive_notification(member)
    ]

    event_data = event_data or {}
    identifier = create_identifier(slug, event_data)
    event_type, _created = EventType.objects.get_or_create(
        team=team, identifier=identifier, defaults={"event_data": event_data, "level": level}
    )

    notification_event = NotificationEvent.objects.create(
        team=team,
        event_type=event_type,
        title=title,
        message=message,
        links=links,
    )

    for user in users:
        if is_notification_muted(user, team, event_type):
            continue

        event_user, created = EventUser.objects.get_or_create(team=team, event_type=event_type, user=user)

        # User will only be notified when first created or when a previously read event is new again.
        user_should_be_notified = created or event_user.read is True
        if event_user.read is True:
            event_user.read = False
            event_user.read_at = None
            event_user.save(update_fields=["read", "read_at"])

        # Bust cache when notification is created or when marking previously read notification as unread.
        if user_should_be_notified:
            bust_unread_notification_cache(user.id, team_slug=team.slug)
            send_notification_email(event_user, notification_event)

    return notification_event


def _notifications_flag_is_active(team: Team) -> bool:
    flag = Flag.objects.filter(name=Flags.NOTIFICATIONS.slug).first()
    return bool(flag and flag.is_active_for_team(team))


def get_user_notification_cache_value(user_id: int, team_slug: str) -> int | None:
    """
    Get the unread notifications count cache for a specific user.
    Args:
        user_id (int): The ID of the user whose cache should be retrieved.
    """
    return cache.get(CACHE_KEY_FORMAT.format(user_id=user_id, team_slug=team_slug))


def set_user_notification_cache(user_id: int, team_slug: str, count: int):
    """
    Set the unread notifications count cache for a specific user.

    Args:
        user_id (int): The ID of the user whose cache should be set.
        count (int): The unread notifications count to cache.
    """
    cache_key = CACHE_KEY_FORMAT.format(user_id=user_id, team_slug=team_slug)
    cache.set(cache_key, count, 5 * 60)  # Cache for 5 minutes


def bust_unread_notification_cache(user_id: int, team_slug: str):
    """
    Bust the unread notifications count cache for a specific user.

    Args:
        user_id (int): The ID of the user whose cache should be busted.
    """
    cache.delete(CACHE_KEY_FORMAT.format(user_id=user_id, team_slug=team_slug))


def send_notification_email(event_user: EventUser, notification_event: NotificationEvent):
    """
    Send an email notification to the user.

    Args:
        user: The user to send the email to
        notification: The notification object containing title, message, and level
    """
    user = event_user.user
    event_type = event_user.event_type

    # Check if user has email notifications enabled and meets minimum level threshold
    try:
        preferences = UserNotificationPreferences.objects.get(user=user, team=event_user.team)
        if not preferences.email_enabled:
            return
        # Ignore if notification level is lower than the user's preference
        if event_type.level < preferences.email_level:
            return

    except UserNotificationPreferences.DoesNotExist:
        return

    subject = f"Notification: {notification_event.title}"

    # Build absolute URL for user profile
    profile_url = absolute_url(reverse("users:user_profile"))

    context = {
        "user": user,
        "notification": notification_event,
        "title": notification_event.title,
        "message": notification_event.message,
        "level": event_type.get_level_display(),
        "profile_url": profile_url,
    }

    # Try to render a template if it exists, otherwise use plain text
    try:
        message = render_to_string("ocs_notifications/email/notification.html", context)
        send_mail(
            subject=subject,
            message="",
            from_email=None,  # Uses DEFAULT_FROM_EMAIL from settings
            recipient_list=[user.email],
            html_message=message,
        )
    except Exception:
        logger.exception("Failed to render email template")
        # Fallback to plain text email
        message = f"{notification_event.title}\n\n{notification_event.message}"
        send_mail(
            subject=subject,
            message=message,
            from_email=None,
            recipient_list=[user.email],
        )


def create_identifier(slug: str, data: dict) -> str:
    """
    Create a unique identifier string based on the provided slug and data dictionary.

    Args:
        slug (str): A slug to identify the notification type.
        data (dict): A dictionary of data to base the identifier on.

    Returns:
        str: A SHA1 hash string representing the slug and data combined.
    """
    combined_data = {"slug": slug, "data": data}
    json_data = json.dumps(combined_data, sort_keys=True)
    return hashlib.sha1(json_data.encode("utf-8")).hexdigest()


def toggle_notification_read(user, event_user: EventUser, read: bool) -> None:
    """
    Mark a specific notification as read for a user.

    Args:
        user: The user whose notification should be marked as read.
        notification_id (int): The ID of the notification to mark as read.
    """

    if event_user.read == read:
        return  # No change needed

    if read:
        event_user.read = True
        event_user.read_at = timezone.now()
    else:
        event_user.read = False
        event_user.read_at = None
    event_user.save()
    bust_unread_notification_cache(user.id, team_slug=event_user.team.slug)


def is_notification_muted(user, team: Team, event_type: EventType) -> bool:
    """
    Check if a user has muted a specific event type. This also returns True if the user enabled Do Not Disturb.

    Args:
        user: The user to check mute status for
        team: The team context
        event_type: The event type to check

    Returns:
        bool: True if notifications are muted, False otherwise
    """
    now = timezone.now()

    if UserNotificationPreferences.objects.filter(user=user, team=team, do_not_disturb_until__gt=now).exists():
        return True

    return EventUser.objects.filter(
        muted_until__gt=now,
        user=user,
        team=team,
        event_type=event_type,
    ).exists()


def mute_notification(user, team: Team, event_type: EventType, timedelta: DurationTimeDelta) -> EventUser:
    """Create or update a notification mute for a user"""
    muted_until = None
    if timedelta.value:
        muted_until = timezone.now() + timedelta.value

    event_user, _ = EventUser.objects.get_or_create(
        user=user,
        team=team,
        event_type=event_type,
    )
    event_user.muted_until = muted_until
    event_user.save(update_fields=["muted_until"])

    return event_user


def unmute_notification(user, team: Team, event_type: EventType) -> None:
    """Clear a notification mute for a user"""
    EventUser.objects.filter(
        user=user,
        team=team,
        event_type=event_type,
    ).update(muted_until=None)
