import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def cleanup_expired_cache_entries():
    """
    Clean up expired cache entries to prevent database bloat.
    Should be run periodically (e.g., hourly).
    """
    from .models import DashboardCache

    expired_count = DashboardCache.objects.filter(expires_at__lt=timezone.now()).delete()[0]

    logger.info(f"Cleaned up {expired_count} expired cache entries")
