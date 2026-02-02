import json
import logging
from base64 import b64encode

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
    event_data: dict | None = None,
    permissions=None,
):
    """
    Create a notification and associate it with the given users.

    Args:
        title (str): The title of the notification.
        message (str): The message content of the notification.
        level (str): The level of the notification (info, warning, error).
        team (Team, optional): A team whose members will be associated with the notification.
        event_data (dict, optional): Additional data to store with the notification.

    Returns:
        Notification: The created Notification instance, or None if creation failed.
    """
    notification = None

    def _can_receive_notification(member):
        if not permissions:
            return True
        return member.has_perms(permissions)

    users = [
        member.user for member in team.membership_set.select_related("user").all() if _can_receive_notification(member)
    ]

    try:
        event_data = event_data or {"message": message}
        identifier = create_identifier(event_data)
        notification, created = Notification.objects.update_or_create(
            team=team,
            title=title,
            message=message,
            level=level,
            identifier=identifier,
            defaults={"last_event_at": timezone.now()},
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

    except Exception:
        logger.exception("Failed to create notification")


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


def create_identifier(data: dict) -> str:
    """
    Create a unique identifier string based on the provided data dictionary.

    Args:
        data (dict): A dictionary of data to base the identifier on.

    Returns:
        str: A base64-encoded JSON string representing the identifier.
    """
    json_data = json.dumps(data, sort_keys=True)
    encoded_data = b64encode(json_data.encode("utf-8")).decode("utf-8")
    return encoded_data


def get_unread_notification_count(user) -> int:
    """
    Get the count of unread notifications for a user.

    Args:
        user: The user to get unread notification count for.

    Returns:
        int: The count of unread notifications.
    """
    return UserNotification.objects.filter(user=user, read=False).count()


def mark_notification_read(user, notification_id: int) -> None:
    """
    Mark a specific notification as read for a user.

    Args:
        user: The user whose notification should be marked as read.
        notification_id (int): The ID of the notification to mark as read.
    """
    user_notification = UserNotification.objects.get(notification_id=notification_id, user=user)
    user_notification.read = True
    user_notification.read_at = timezone.now()
    user_notification.save()
