from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.assessments.models import Score

if TYPE_CHECKING:
    from apps.evaluations.models import EvaluationResult
    from apps.human_annotations.models import Annotation

logger = logging.getLogger(__name__)


def _score_from_field(
    *,
    team,
    target,
    name: str,
    raw_value: Any,
    source: str,
    automated_result=None,
    review=None,
    author=None,
    schema_field: dict | None = None,
) -> Score | None:
    """Build an unsaved Score for one schema field. Returns None for unsupported values."""
    if raw_value is None:
        return None

    schema_type = (schema_field or {}).get("type")
    value_numeric: Decimal | None = None
    value_string: str | None = None

    # Force categorical when the schema declares a choice — handles numeric-looking
    # choice values like "0" / "1" without misclassifying them as numeric.
    if schema_type == "choice":
        data_type = Score.DataType.CATEGORICAL
        value_string = str(raw_value)
    elif isinstance(raw_value, bool):
        data_type = Score.DataType.BOOLEAN
        value_numeric = Decimal(1) if raw_value else Decimal(0)
    elif isinstance(raw_value, int | float | Decimal):
        try:
            value_numeric = Decimal(str(raw_value))
        except InvalidOperation:
            logger.warning("Score field %s: cannot convert %r to Decimal; skipping", name, raw_value)
            return None
        data_type = Score.DataType.NUMERIC
    elif isinstance(raw_value, str):
        data_type = Score.DataType.CATEGORICAL
        value_string = raw_value
    else:
        logger.warning(
            "Score field %s: unsupported value type %s; skipping (v1 only supports bool/int/float/str)",
            name,
            type(raw_value).__name__,
        )
        return None

    return Score(
        team=team,
        target_content_type=ContentType.objects.get_for_model(target),
        target_object_id=target.pk,
        name=name,
        data_type=data_type,
        value_numeric=value_numeric,
        value_string=value_string,
        source=source,
        automated_result=automated_result,
        review=review,
        author=author,
    )


def _source_for_evaluator(evaluator) -> str:
    """Map an Evaluator.type to a Score.Source. Defaults to LLM_JUDGE."""
    if evaluator.type == "PythonEvaluator":
        return Score.Source.PROGRAMMATIC
    return Score.Source.LLM_JUDGE


def write_scores_from_evaluation_result(result: EvaluationResult) -> None:
    """Decompose an EvaluationResult's output into Score rows.

    Idempotent: deletes existing Scores for this result then bulk-creates fresh ones.
    No-op when the result has no associated ExperimentSession or when the output
    contains an error payload.
    """
    output = result.output or {}
    if "error" in output:
        return
    session = result.message.session if result.message_id else None
    if session is None:
        return

    result_payload = output.get("result", {}) or {}
    if not isinstance(result_payload, dict):
        return

    source = _source_for_evaluator(result.evaluator)
    schema = (result.evaluator.params or {}).get("output_schema", {}) or {}

    scores = []
    for name, raw_value in result_payload.items():
        score = _score_from_field(
            team=result.team,
            target=session,
            name=name,
            raw_value=raw_value,
            source=source,
            automated_result=result,
            schema_field=schema.get(name),
        )
        if score is not None:
            scores.append(score)

    with transaction.atomic():
        Score.objects.filter(automated_result=result).delete()
        Score.objects.bulk_create(scores)


def write_scores_from_annotation(annotation: Annotation) -> None:
    """Decompose an Annotation's data dict into Score rows.

    Idempotent: deletes existing Scores for this annotation then bulk-creates fresh ones.
    Skips message-only items (D-13 in the unified design excludes ChatMessage as a
    Score target). Writes Scores regardless of `is_authoritative` — the concordance
    view filters to authoritative at read time so non-authoritative annotations are
    preserved for future inter-rater-reliability work.
    """
    item = annotation.item
    target = item.session  # v1 only targets ExperimentSession; ChatMessage skipped
    if target is None:
        return

    schema = item.queue.schema or {}
    data = annotation.data or {}

    scores = []
    for name, raw_value in data.items():
        score = _score_from_field(
            team=annotation.team,
            target=target,
            name=name,
            raw_value=raw_value,
            source=Score.Source.HUMAN_REVIEW,
            review=annotation,
            author=annotation.reviewer,
            schema_field=schema.get(name),
        )
        if score is not None:
            scores.append(score)

    with transaction.atomic():
        Score.objects.filter(review=annotation).delete()
        Score.objects.bulk_create(scores)
