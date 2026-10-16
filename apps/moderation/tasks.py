import logging

from celery import shared_task
from openai import APIConnectionError, InternalServerError
from pydantic import ValidationError

from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import ModerationProvider
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.utils.celery import Queues

logger = logging.getLogger("ocs.moderation")

REQUEST_TIMEOUT_SECONDS = 2
"""Timeout for one provider request.

With 2 retries after 1 and 2 seconds, the task takes at most 2 + 1 + 2 + 2 + 2 = 9 seconds, inside
`MODERATION_WAIT_TIMEOUT`.
"""


@shared_task(
    queue=Queues.BACKGROUND,
    autoretry_for=(APIConnectionError, InternalServerError),
    max_retries=2,
    retry_backoff=1,
    retry_jitter=False,
)
def moderate_message(*, text: str, moderation_provider_id: int) -> dict:
    """Run the text through the moderation provider. Writes nothing to the database.

    Returns `{"status": "ok", "output": ...}` or `{"status": "skipped", "reason": ...}`. Raises the
    connection or server error when the last retry fails.
    """
    try:
        provider = ModerationProvider.objects.get(id=moderation_provider_id)
        service = provider.get_moderation_service(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        output = service.moderate(text)
    except ModerationRateLimited:
        logger.warning("Moderation provider %s is rate limited", moderation_provider_id)
        return _skipped("rate_limited")
    except (ModerationProvider.DoesNotExist, ServiceProviderConfigError, ValidationError):
        logger.exception("Could not moderate a message with provider %s", moderation_provider_id)
        return _skipped("error")
    return {"status": "ok", "output": output.model_dump(mode="json")}


def _skipped(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}
