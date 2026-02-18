import hashlib
import json
import logging
from enum import Enum

from django.core.cache import cache
from django.db.models import Exists, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ocs_notifications.models import (
    EventType,
    EventUser,
    LevelChoices,
    NotificationEvent,
    UserNotificationPreferences,
)
from apps.ocs_notifications.tasks import send_notification_email_async
from apps.teams.flags import Flags
from apps.teams.models import Flag, Team

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

    user_info = get_users_to_be_notified(team, permissions)
    users = list(user_info.keys())

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

    existing_event_users = {
        eu.user: eu for eu in EventUser.objects.filter(team=team, event_type=event_type, user__in=users)
    }
    event_users_to_create = []
    event_users_to_update = []
    users_to_email = []

    for user in users:
        event_user = existing_event_users.get(user)
        if is_notification_muted(event_user):
            continue

        should_notify_user = True
        if event_user:
            # User will only be notified when first created or when a previously read event is detected again.
            if event_user.read is True:
                event_user.read = False
                event_user.read_at = None
                event_users_to_update.append(event_user)
            else:
                should_notify_user = False
        else:
            event_user = EventUser(team=team, event_type=event_type, user=user)
            event_users_to_create.append(event_user)

        # Bust cache when notification is created or when marking previously read notification as unread.
        if should_notify_user:
            # Busting the cache so that the UI can show the updated unread notifications count immediately
            bust_unread_notification_cache(user.id, team_slug=team.slug)
            if should_send_email(user_info[user], event_level=level):
                users_to_email.append(user.id)

    EventUser.objects.bulk_create(event_users_to_create)
    EventUser.objects.bulk_update(event_users_to_update, fields=["read", "read_at"])

    send_notification_email_async.delay(users_to_email, notification_event_id=notification_event.id)
    return notification_event


def get_users_to_be_notified(team: Team, permissions: list[str]) -> dict:
    def _is_notification_target(member):
        if not permissions:
            return True
        return member.has_perms(permissions)

    now = timezone.now()

    # Subquery to check if the user has Do Not Disturb enabled for this team and time
    do_not_disturb_subquery = UserNotificationPreferences.objects.filter(
        user=OuterRef("user_id"), team=team, do_not_disturb_until__gt=now
    )

    # Subquery to get the user's email_enabled preference for this team
    email_enabled_subquery = UserNotificationPreferences.objects.filter(user=OuterRef("user_id"), team=team).values(
        "email_enabled"
    )[:1]

    # Subquery to get the user's email_level preference for this team
    email_level_subquery = UserNotificationPreferences.objects.filter(user=OuterRef("user_id"), team=team).values(
        "email_level"
    )[:1]

    # Only include members who do NOT have DND enabled (do_not_disturb=False)
    members_qs = (
        team.membership_set.select_related("user")
        .annotate(
            do_not_disturb=Exists(do_not_disturb_subquery),
            email_enabled=Coalesce(Subquery(email_enabled_subquery), False),
            email_level=Coalesce(Subquery(email_level_subquery), LevelChoices.INFO.value),
        )
        .filter(do_not_disturb=False)
    )

    return {
        member.user: {
            "email_enabled": member.email_enabled,
            "email_level": member.email_level,
        }
        for member in members_qs
        if _is_notification_target(member)
    }


def should_send_email(user_email_info: dict, event_level) -> bool:
    return user_email_info["email_enabled"] and event_level >= user_email_info["email_level"]


def _notifications_flag_is_active(team: Team) -> bool:
    key = f"notifications_flag_{team.id}"
    is_active = cache.get(key, default=None)
    if is_active is None:
        flag = Flag.objects.filter(name=Flags.NOTIFICATIONS.slug).first()
        is_active = bool(flag and flag.is_active_for_team(team))
        cache.set(key, is_active, 30)  # Cache the flag status for 30 seconds
    return is_active


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


def is_notification_muted(event_user: EventUser | None) -> bool:
    """
    Check if a user has muted a specific event type.

    Args:
        user: The user to check mute status for
        team: The team context
        event_type: The event type to check

    Returns:
        bool: True if notifications are muted, False otherwise
    """
    return bool(event_user and event_user.muted_until is not None and event_user.muted_until > timezone.now())


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
