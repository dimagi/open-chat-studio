import logging

from celery import shared_task

from apps.assistants.sync import OpenAiSyncError, delete_openai_assistant

logger = logging.getLogger("ocs.openai_sync")


@shared_task(
    max_retries=5,
    retry_backoff=True,
    acks_late=True,
    bind=True,
)
def delete_openai_assistant_task(self, assistant_id: int):
    # lazy import to avoid import on startup
    from openai import (
        APIError,
        APIResponseValidationError,
        BadRequestError,
        NotFoundError,
        PermissionDeniedError,
        UnprocessableEntityError,
    )

    from apps.assistants.models import OpenAiAssistant

    try:
        assistant = OpenAiAssistant.all_objects.get(id=assistant_id, is_archived=True)
    except OpenAiAssistant.DoesNotExist:
        if not self.request.retries:
            # Edge case where the archive DB transaction hasn't completed before the task starts
            self.retry(countdown=5)
        logger.warning("Assistant with id %s not found or not archived, skipping deletion", assistant_id)
        return

    no_retry_errors = (
        APIResponseValidationError,
        BadRequestError,
        NotFoundError,
        PermissionDeniedError,
        UnprocessableEntityError,
    )
    try:
        delete_openai_assistant(assistant)
    except no_retry_errors:
        raise
    except APIError as e:
        raise self.retry(exc=e) from None
    except OpenAiSyncError as e:
        cause = e.__context__
        if isinstance(cause, no_retry_errors):
            raise cause from None
        if isinstance(cause, APIError):
            raise self.retry(exc=cause) from None
        raise cause from None
