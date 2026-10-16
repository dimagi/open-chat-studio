import logging

from celery import shared_task
from openai import APIConnectionError, InternalServerError
from pydantic import ValidationError

from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import ModerationProvider
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.utils.celery import Queues

logger = logging.getLogger("ocs.moderation")

REQUEST_TIMEOUT_SECONDS = 3
"""Timeout for one provider request. With the retries, the task finishes within about `MODERATION_WAIT_TIMEOUT`."""

RETRY_BACKOFF_SECONDS = 1
"""Delay before the first retry; it doubles for each later retry."""


@shared_task(bind=True, queue=Queues.BACKGROUND, max_retries=2)
def moderate_message(self, *, text: str, moderation_provider_id: int) -> dict:
    """Run the text through the moderation provider. Writes nothing to the database.

    Returns `{"status": "ok", "output": ...}` or `{"status": "skipped", "reason": ...}`.
    """
    try:
        provider = ModerationProvider.objects.get(id=moderation_provider_id)
        service = provider.get_moderation_service(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        output = service.moderate(text)
    except ModerationRateLimited:
        logger.warning("Moderation provider %s is rate limited", moderation_provider_id)
        return _skipped("rate_limited")
    except (APIConnectionError, InternalServerError) as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=RETRY_BACKOFF_SECONDS * 2**self.request.retries) from e
        logger.warning("Moderation provider %s failed after retries: %s", moderation_provider_id, e)
        return _skipped("error")
    except (ModerationProvider.DoesNotExist, ServiceProviderConfigError, ValidationError):
        logger.exception("Could not moderate a message with provider %s", moderation_provider_id)
        return _skipped("error")
    return {"status": "ok", "output": output.model_dump(mode="json")}


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}
