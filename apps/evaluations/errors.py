"""Failed evaluation results: how a failure is recorded on a result, categorised, and summarised for a run.

A result fails in one of two ways. The evaluator raised, and the output is ``{"error": ...}``
in place of a score. Or the chatbot failed to generate a response: the evaluator still judged
what came back (empty, or the channel's canned error reply), and the output carries
``generation_error`` beside that score, which then describes nothing about the bot.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter

from celery.exceptions import SoftTimeLimitExceeded
from django.db import models
from django.db.models import Case, Q, QuerySet, TextField, Value, When
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce

from apps.service_providers.llm_service.error_classification import classify_provider_error
from apps.service_providers.llm_service.structured_output import NoStructuredOutputError

ERROR_KEY = "error"
ERROR_CATEGORY_KEY = "error_category"
GENERATION_ERROR_KEY = "generation_error"
GENERATION_ERROR_CATEGORY_KEY = "generation_error_category"

FAILED_OUTPUT_Q = Q(output__has_key=ERROR_KEY) | Q(output__has_key=GENERATION_ERROR_KEY)

ERROR_WARNING_RATE = 0.05  # a run warns when more than this share of its results failed


class EvaluationErrorSource(models.TextChoices):
    GENERATION = "generation", "Generation failed"
    EVALUATOR = "evaluator", "Evaluator failed"


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

    @property
    def hint(self) -> str:
        return ERROR_CATEGORY_HINTS[self]


ERROR_CATEGORY_HINTS = {
    EvaluationErrorCategory.QUOTA_EXHAUSTED: (
        "The provider account has no credit left or has reached its usage limit. "
        "Add credit or raise the limit, then run the evaluation again."
    ),
    EvaluationErrorCategory.RATE_LIMIT: "The provider limited the request rate. Run the evaluation again later.",
    EvaluationErrorCategory.AUTHENTICATION: (
        "The provider rejected the API key. Check the service provider's credentials."
    ),
    EvaluationErrorCategory.MODEL_NOT_FOUND: "The provider does not offer the configured model. Choose another model.",
    EvaluationErrorCategory.CONTEXT_OVERFLOW: "The conversation was too long for the model's context window.",
    EvaluationErrorCategory.TIMEOUT: "The request took too long. Running the evaluation again usually succeeds.",
    EvaluationErrorCategory.PROVIDER_UNAVAILABLE: (
        "The provider was down or overloaded. Running the evaluation again usually succeeds."
    ),
    EvaluationErrorCategory.INVALID_REQUEST: "The provider rejected the request. Open a failed result for the details.",
    EvaluationErrorCategory.INVALID_OUTPUT: (
        "The evaluator's model did not return its answer in the expected format. "
        "Check the evaluator's prompt and output fields."
    ),
    EvaluationErrorCategory.OTHER: "Open a failed result for the details.",
}


@dataclass(frozen=True)
class MessageError:
    source: EvaluationErrorSource
    category: EvaluationErrorCategory
    message: str
    evaluator_name: str | None = None  # None for a generation failure


@dataclass(frozen=True)
class ErrorGroup:
    source: EvaluationErrorSource
    category: EvaluationErrorCategory
    count: int


@dataclass(frozen=True)
class RunErrorSummary:
    """A run's failed messages. Counted by message, the unit the results table shows."""

    failed_count: int
    total_count: int
    groups: list[ErrorGroup]

    @property
    def needs_warning(self) -> bool:
        return bool(self.total_count) and self.failed_count / self.total_count > ERROR_WARNING_RATE

    def describe(self) -> str:
        """The groups as one line, e.g. "26 generation failed: Provider quota exhausted; 2 evaluator failed: …"."""
        return "; ".join(f"{group.count} {group.source.label.lower()}: {group.category.label}" for group in self.groups)


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


def message_errors(results: Iterable[tuple[str | None, dict | None]]) -> list[MessageError]:
    """The failures on one message, from its results as (evaluator name, output) pairs.

    A generation failure is copied onto every evaluator's result for the message, so it is
    listed once, first, since it is the cause of anything after it. Each evaluator's own
    failure follows, carrying that evaluator's name.
    """
    errors: list[MessageError] = []
    generation: MessageError | None = None
    for evaluator_name, output in results:
        output = output or {}
        if generation is None and GENERATION_ERROR_KEY in output:
            generation = MessageError(
                source=EvaluationErrorSource.GENERATION,
                category=_category(output.get(GENERATION_ERROR_CATEGORY_KEY)),
                message=output[GENERATION_ERROR_KEY],
            )
        if ERROR_KEY in output:
            errors.append(
                MessageError(
                    source=EvaluationErrorSource.EVALUATOR,
                    category=_category(output.get(ERROR_CATEGORY_KEY)),
                    message=output[ERROR_KEY],
                    evaluator_name=evaluator_name,
                )
            )
    return [generation, *errors] if generation else errors


def summarize_errors(results: QuerySet) -> RunErrorSummary:
    """Count a run's failed messages by source and category, largest group first.

    Each message is counted once, under its first failure from `message_errors`. Only the
    error keys are read, since a result's output also carries a copy of the whole message.
    """
    error_keys = (GENERATION_ERROR_KEY, GENERATION_ERROR_CATEGORY_KEY, ERROR_KEY, ERROR_CATEGORY_KEY)
    rows = (
        results.filter(FAILED_OUTPUT_Q)
        .annotate(**{f"_{key}": _stored_value(key) for key in error_keys})
        .order_by("message_id", "evaluator_id")
        .values_list("message_id", *(f"_{key}" for key in error_keys))
    )
    counts: Counter[tuple[EvaluationErrorSource, EvaluationErrorCategory]] = Counter()
    for _message_id, message_rows in groupby(rows.iterator(), key=itemgetter(0)):
        outputs = (
            (None, {key: value for key, value in zip(error_keys, row[1:], strict=True) if value is not None})
            for row in message_rows
        )
        cause = message_errors(outputs)[0]
        counts[(cause.source, cause.category)] += 1
    groups = [
        ErrorGroup(source=source, category=category, count=count) for (source, category), count in counts.most_common()
    ]
    total_count = results.values("message_id").distinct().count()
    return RunErrorSummary(failed_count=sum(counts.values()), total_count=total_count, groups=groups)


def _stored_value(key: str) -> Case:
    """The output's value for `key` as text, or NULL when the key is missing.

    `KT` alone reads a key stored as JSON null the same as a missing one, but the key
    being present is what marks a result as failed.
    """
    return Case(
        When(Q(output__has_key=key), then=Coalesce(KT(f"output__{key}"), Value(""))),
        output_field=TextField(),
    )
