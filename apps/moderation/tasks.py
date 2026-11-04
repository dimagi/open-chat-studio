import logging

from celery import shared_task
from openai import APIConnectionError, InternalServerError
from pydantic import ValidationError

from apps.chat.models import ChatMessage
from apps.moderation.const import REQUEST_TIMEOUT_SECONDS
from apps.moderation.datamodels import ModerationTaskResult
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import ModerationProvider
from apps.service_providers.moderation_service import ModerationRateLimited
from apps.utils.celery import Queues

logger = logging.getLogger("ocs.moderation")


# The retries must finish inside `MODERATION_WAIT_TIMEOUT`; `test_retries_finish_before_the_wait_times_out`
# checks this.
@shared_task(
    queue=Queues.BACKGROUND,
    autoretry_for=(APIConnectionError, InternalServerError),
    max_retries=2,
    retry_backoff=1,
    retry_jitter=False,
)
def moderate_message(*, chat_message_id: int, moderation_provider_id: int) -> dict:
    """Run the message's text through the moderation provider. Writes nothing to the database.

    Returns a `ModerationTaskResult` as a dict. Raises the connection or server error when the last
    retry fails.
    """
    try:
        text = ChatMessage.objects.values_list("content", flat=True).get(id=chat_message_id)
        provider = ModerationProvider.objects.get(id=moderation_provider_id)
        service = provider.get_moderation_service(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        output = service.moderate(text)
    except ModerationRateLimited:
        logger.warning("Moderation provider %s is rate limited", moderation_provider_id)
        return ModerationTaskResult(skip_reason="rate_limited").model_dump(mode="json")
    except (ChatMessage.DoesNotExist, ModerationProvider.DoesNotExist, ServiceProviderConfigError, ValidationError):
        logger.exception("Could not moderate message %s with provider %s", chat_message_id, moderation_provider_id)
        return ModerationTaskResult(skip_reason="error").model_dump(mode="json")
    return ModerationTaskResult(output=output).model_dump(mode="json")
