"""Failed evaluation results: how a failure is recorded on a result and categorised.

A result fails in one of two ways. The evaluator raised, and the output is ``{"error": ...}``
in place of a score. Or the chatbot failed to generate a response: the evaluator still judged
what came back (empty, or the channel's canned error reply), and the output carries
``generation_error`` beside that score, which then describes nothing about the bot.
"""

from celery.exceptions import SoftTimeLimitExceeded
from django.db import models

from apps.service_providers.llm_service.error_classification import classify_provider_error
from apps.service_providers.llm_service.structured_output import NoStructuredOutputError

ERROR_KEY = "error"
ERROR_CATEGORY_KEY = "error_category"
GENERATION_ERROR_KEY = "generation_error"
GENERATION_ERROR_CATEGORY_KEY = "generation_error_category"


class EvaluationErrorCategory(models.TextChoices):
    QUOTA_EXHAUSTED = "quota_exhausted", "Provider quota exhausted"
    RATE_LIMIT = "rate_limit", "Provider rate limit"
    AUTHENTICATION = "authentication", "Provider credentials rejected"
    MODEL_NOT_FOUND = "model_not_found", "Model not found"
    CONTEXT_OVERFLOW = "context_overflow", "Context window exceeded"
    TIMEOUT = "timeout", "Timed out"
    PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
    INVALID_REQUEST = "invalid_request", "Request rejected by provider"
    INVALID_OUTPUT = "invalid_output", "No structured output"
    OTHER = "other", "Other error"


def classify_evaluation_error(error: BaseException) -> EvaluationErrorCategory:
    if isinstance(error, NoStructuredOutputError):
        return EvaluationErrorCategory.INVALID_OUTPUT
    if isinstance(error, SoftTimeLimitExceeded):
        return EvaluationErrorCategory.TIMEOUT
    if kind := classify_provider_error(error):
        return _category(kind.value)
    return EvaluationErrorCategory.OTHER


def _category(value: str | None) -> EvaluationErrorCategory:
    """The category a stored value names, or OTHER (legacy, removed, or unmapped values)."""
    try:
        return EvaluationErrorCategory(value)
    except ValueError:
        return EvaluationErrorCategory.OTHER


def evaluator_error_output(error: BaseException) -> dict:
    """The output stored in place of a score when the evaluator raised."""
    return {ERROR_KEY: _message(error), ERROR_CATEGORY_KEY: classify_evaluation_error(error).value}


def generation_error_fields(error: BaseException) -> dict:
    """The fields added to every result for a message whose generation failed."""
    return {
        GENERATION_ERROR_KEY: _message(error),
        GENERATION_ERROR_CATEGORY_KEY: classify_evaluation_error(error).value,
    }


def _message(error: BaseException) -> str:
    # Some exceptions have no message at all, e.g. a bare `raise ValueError()`.
    return str(error) or type(error).__name__


def is_failed_output(output: dict | None) -> bool:
    return bool(output) and (ERROR_KEY in output or GENERATION_ERROR_KEY in output)
