from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from celery import shared_task
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.ocs_notifications.models import (
    NotificationEvent,
)
from apps.web.meta import absolute_url

if TYPE_CHECKING:
    from apps.users.models import CustomUser

logger = logging.getLogger("ocs.notifications")


@shared_task
def send_notification_email_async(user_ids, notification_event_id):
    from apps.users.models import CustomUser

    try:
        users = CustomUser.objects.filter(id__in=user_ids)
        notification_event = NotificationEvent.objects.select_related("event_type").get(id=notification_event_id)
        send_notification_email(users, notification_event)
    except Exception:
        logger.exception("Failed to send notification email async")


def send_notification_email(users: list[CustomUser], notification_event: NotificationEvent):
    """
    Send an email notification to the user.

    Args:
        user: The user to send the email to
        notification: The notification object containing title, message, and level
    """
    for user in users:
        subject = f"Notification: {notification_event.title}"

        # Build absolute URL for user profile
        profile_url = absolute_url(reverse("users:user_profile"))

        context = {
            "user": user,
            "notification": notification_event,
            "title": notification_event.title,
            "message": notification_event.message,
            "level": notification_event.event_type.get_level_display(),
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


@shared_task(ignore_result=True)
def cleanup_old_notification_events():
    """Delete NotificationEvent records older than 3 months."""
    three_months_ago = timezone.now() - timedelta(days=90)
    NotificationEvent.objects.filter(created_at__lt=three_months_ago).delete()
