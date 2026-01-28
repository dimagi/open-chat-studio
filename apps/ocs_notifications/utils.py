import logging

from django.core.cache import cache

from apps.teams.models import Team

from .models import CategoryChoices, Notification

logger = logging.getLogger("ocs.notifications")

CACHE_KEY_FORMAT = "{user_id}-unread-notifications-count"


def create_notification(
    title: str, message: str, category: CategoryChoices, users: list | None = None, team: Team | None = None, link=None
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
        notification = Notification.objects.create(
            title=title,
            message=message,
            category=category,
        )
        for user in users:
            if category == CategoryChoices.ERROR:
                # Bust cache for errors
                cache_key = f"{user.id}-unread-notifications-count"
                cache.delete(cache_key)

            user.notifications.add(notification)
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
