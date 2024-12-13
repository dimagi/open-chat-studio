import logging

import openai
from celery import shared_task

from apps.assistants.sync import OpenAiSyncError, delete_openai_assistant

logger = logging.getLogger("openai_sync")


@shared_task(
    autoretry_for=(openai.APIError,),
    dont_autoretry_for=(
        openai.APIResponseValidationError,
        openai.BadRequestError,
        openai.NotFoundError,
        openai.PermissionDeniedError,
        openai.UnprocessableEntityError,
    ),
    max_retries=5,
    retry_backoff=True,
    acks_late=True,
)
def delete_openai_assistant_task(assistant_id: int):
    from apps.assistants.models import OpenAiAssistant

    try:
        assistant = OpenAiAssistant.all_objects.get(id=assistant_id, is_archived=True)
    except OpenAiAssistant.DoesNotExist:
        logger.warning("Assistant with id %s not found or not archived, skipping deletion", assistant_id)
        return

    try:
        delete_openai_assistant(assistant)
    except OpenAiSyncError as e:
        # re-raise the original error for retry purposes
        raise e.__context__ from None
