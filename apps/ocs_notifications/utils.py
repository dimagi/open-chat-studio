import json
import logging
from base64 import b64encode

from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from apps.teams.models import Team

from .models import LevelChoices, Notification, UserNotification, UserNotificationPreferences

logger = logging.getLogger("ocs.notifications")

CACHE_KEY_FORMAT = "{user_id}-unread-notifications-count"


def create_notification(
    title: str,
    message: str,
    level: LevelChoices,
    users: list | None = None,
    team: Team | None = None,
    link=None,
    data: dict | None = None,
):
    """
    Create a notification and associate it with the given users.

    Args:
        title (str): The title of the notification.
        message (str): The message content of the notification.
        category (str): The category of the notification (info, warning, error).
        users (list): A list of user instances to associate with the notification.
        link (str, optional): An optional link related to the notification.

    Returns:
        Notification: The created Notification instance.
    """
    users = users or []
    if team:
        users.extend([member.user for member in team.membership_set.select_related("user").all()])

    users = set(users)

    try:
        identifier = create_identifier(data) if data else None
        notification, created = Notification.objects.update_or_create(
            title=title,
            message=message,
            level=level,
            identifier=identifier,
            defaults={"last_event_at": timezone.now()},
        )
        for user in users:
            if level == LevelChoices.ERROR:
                bust_unread_notification_cache(user.id)

            user_notification, created = UserNotification.objects.get_or_create(notification=notification, user=user)
            # Email will only be sent when the notification is newly created or if the notification was previously read
            should_send_email = created or user_notification.read is True
            user_notification.read = False
            user_notification.read_at = None
            user_notification.save()

            if should_send_email:
                send_notification_email(user_notification)

    except Exception:
        logger.exception("Failed to create notification")

    return notification


# TODO: Test these methods
def get_user_notification_cache_value(user_id: int) -> int | None:
    """
    Get the unread notifications count cache for a specific user.
    Args:
        user_id (int): The ID of the user whose cache should be retrieved.
    """
    return cache.get(CACHE_KEY_FORMAT.format(user_id=user_id))


def set_user_notification_cache(user_id: int, count: int):
    """
    Set the unread notifications count cache for a specific user.

    Args:
        user_id (int): The ID of the user whose cache should be set.
        count (int): The unread notifications count to cache.
    """
    cache_key = CACHE_KEY_FORMAT.format(user_id=user_id)
    cache.set(cache_key, count, 5 * 60)  # Cache for 5 minutes


def bust_unread_notification_cache(user_id: int):
    """
    Bust the unread notifications count cache for a specific user.

    Args:
        user_id (int): The ID of the user whose cache should be busted.
    """
    cache.delete(CACHE_KEY_FORMAT.format(user_id=user_id))


def send_notification_email(user_notification: UserNotification):
    """
    Send an email notification to the user.

    Args:
        user: The user to send the email to
        notification: The notification object containing title, message, and level
    """
    user = user_notification.user
    notification = user_notification.notification
    if not UserNotificationPreferences.objects.filter(user=user_notification.user, email_enabled=True).exists():
        return

    subject = f"Notification: {notification.title}"

    context = {
        "user": user,
        "notification": notification,
        "title": notification.title,
        "message": notification.message,
        "level": notification.get_level_display(),
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
