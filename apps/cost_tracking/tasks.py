"""Celery tasks for cost tracking. The weekly digest is the only one
today; scheduled in `config.settings.SCHEDULED_TASKS`.
"""

import logging
from datetime import timedelta

from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from apps.cost_tracking.services.digest import DigestSummary, build_digest

logger = logging.getLogger("ocs.cost_tracking")

_DIGEST_WINDOW_DAYS = 7


@shared_task(ignore_result=True)
def send_unpriced_usage_digest() -> None:
    """Email the platform team a weekly summary of unpriced models and
    unknown calls. Skips the email when the digest is empty so an inbox
    isn't filled with no-news messages."""
    recipient = _operator_email()
    if not recipient:
        logger.warning("cost_tracking.digest.no_recipient")
        return
    end = timezone.now()
    start = end - timedelta(days=_DIGEST_WINDOW_DAYS)
    summary = build_digest(start, end)
    if summary.is_empty:
        logger.info("cost_tracking.digest.skipped_empty", extra={"start": start.isoformat()})
        return
    send_mail(
        subject=_subject(summary),
        message=render_to_string("cost_tracking/digest_email.txt", {"summary": summary}),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )


def _operator_email() -> str:
    """`settings.COST_TRACKING_OPERATOR_EMAIL` overrides; falls back to the
    project CONTACT_EMAIL so a fresh install routes somewhere sensible."""
    return getattr(settings, "COST_TRACKING_OPERATOR_EMAIL", None) or settings.PROJECT_METADATA.get("CONTACT_EMAIL", "")


def _subject(summary: DigestSummary) -> str:
    return (
        f"[OCS Cost Tracking] {summary.distinct_unpriced_models} unpriced models, "
        f"{summary.total_unknown_calls} unknown calls (last {_DIGEST_WINDOW_DAYS} days)"
    )
