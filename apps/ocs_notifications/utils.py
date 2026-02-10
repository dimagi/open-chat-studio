import hashlib
import json
import logging

from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.teams.models import Team
from apps.web.meta import absolute_url

from .models import LevelChoices, Notification, UserNotification, UserNotificationPreferences

logger = logging.getLogger("ocs.notifications")

CACHE_KEY_FORMAT = "{user_id}-{team_slug}-unread-notifications-count"


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
    Create a notification and associate it with the given users.

    Args:
        title (str): The title of the notification.
        message (str): The message content of the notification.
        level (str): The level of the notification (info, warning, error).
        team (Team, optional): A team whose members will be associated with the notification.
        slug (str): A slug to identify the notification type. Used with event_data for uniqueness.
        event_data (dict, optional): Additional data to store with the notification. Combined with slug for uniqueness.

    Returns:
        Notification: The created Notification instance, or None if creation failed.
    """
    notification = None
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
    notification, created = Notification.objects.update_or_create(
        team=team,
        identifier=identifier,
        defaults={
            "title": title,
            "message": message,
            "level": level,
            "last_event_at": timezone.now(),
            "event_data": event_data,
            "links": links,
        },
    )
    for user in users:
        user_notification, created = UserNotification.objects.get_or_create(
            team=team, notification=notification, user=user
        )
        # Uuser will only be notified when notification is created or if the notification was previously read
        user_should_be_notified = created or user_notification.read is True
        if user_notification.read is True:
            user_notification.read = False
            user_notification.read_at = None
            user_notification.save()

        # Bust cache when notification is created or when marking previously read notification as unread
        if user_should_be_notified:
            bust_unread_notification_cache(user.id, team_slug=team.slug)
            send_notification_email(user_notification)


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


def send_notification_email(user_notification: UserNotification):
    """
    Send an email notification to the user.

    Args:
        user: The user to send the email to
        notification: The notification object containing title, message, and level
    """
    user = user_notification.user
    notification = user_notification.notification

    # Check if user has email notifications enabled and meets minimum level threshold
    try:
        preferences = UserNotificationPreferences.objects.get(user=user, team=user_notification.team)
        if not preferences.email_enabled:
            return
        # Ignore if notification level is lower than the user's preference
        if notification.level < preferences.email_level:
            return

    except UserNotificationPreferences.DoesNotExist:
        return

    subject = f"Notification: {notification.title}"

    # Build absolute URL for user profile
    profile_url = absolute_url(reverse("users:user_profile"))

    context = {
        "user": user,
        "notification": notification,
        "title": notification.title,
        "message": notification.message,
        "level": notification.get_level_display(),
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
        message = f"{notification.title}\n\n{notification.message}"
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


def toggle_notification_read(user, user_notification: UserNotification, read: bool) -> None:
    """
    Mark a specific notification as read for a user.

    Args:
        user: The user whose notification should be marked as read.
        notification_id (int): The ID of the notification to mark as read.
    """

    if user_notification.read == read:
        return  # No change needed

    if read:
        user_notification.read = True
        user_notification.read_at = timezone.now()
    else:
        user_notification.read = False
        user_notification.read_at = None
    user_notification.save()
    bust_unread_notification_cache(user.id, team_slug=user_notification.team.slug)
