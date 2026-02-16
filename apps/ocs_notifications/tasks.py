import logging

from celery import shared_task
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from apps.ocs_notifications.models import (
    EventUser,
    NotificationEvent,
)
from apps.web.meta import absolute_url

logger = logging.getLogger("ocs.notifications")


@shared_task
def send_notification_email_async(event_user_id, notification_event_id):
    try:
        event_user = EventUser.objects.select_related("user", "event_type", "team").get(id=event_user_id)
        notification_event = NotificationEvent.objects.get(id=notification_event_id)
        send_notification_email(event_user, notification_event)
    except Exception:
        logger.exception("Failed to send notification email async")


def send_notification_email(event_user: EventUser, notification_event: NotificationEvent):
    """
    Send an email notification to the user.

    Args:
        user: The user to send the email to
        notification: The notification object containing title, message, and level
    """
    user = event_user.user
    event_type = event_user.event_type

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
