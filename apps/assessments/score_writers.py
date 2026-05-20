import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.contenttypes.models import ContentType

from apps.assessments.models import Score

logger = logging.getLogger(__name__)


def _build_score(
    *,
    team,
    target,
    name: str,
    source: str,
    automated_result,
    review,
    author,
    data_type: str,
    value_numeric: Decimal | None = None,
    value_string: str | None = None,
) -> Score:
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
    schema_type = (schema_field or {}).get("type")

    # Force categorical when the schema declares a choice — handles numeric-looking
    # choice values like "0" / "1" without misclassifying them as numeric.
    if schema_type == "choice":
        return _build_score(
            team=team,
            target=target,
            name=name,
            data_type=Score.DataType.CATEGORICAL,
            value_string=str(raw_value),
            source=source,
            automated_result=automated_result,
            review=review,
            author=author,
        )

    if isinstance(raw_value, bool):
        return _build_score(
            team=team,
            target=target,
            name=name,
            data_type=Score.DataType.BOOLEAN,
            value_numeric=Decimal(1) if raw_value else Decimal(0),
            source=source,
            automated_result=automated_result,
            review=review,
            author=author,
        )

    if isinstance(raw_value, int | float | Decimal):
        try:
            value_numeric = Decimal(str(raw_value))
        except InvalidOperation:
            logger.warning("Score field %s: cannot convert %r to Decimal; skipping", name, raw_value)
            return None
        return _build_score(
            team=team,
            target=target,
            name=name,
            data_type=Score.DataType.NUMERIC,
            value_numeric=value_numeric,
            source=source,
            automated_result=automated_result,
            review=review,
            author=author,
        )

    if isinstance(raw_value, str):
        return _build_score(
            team=team,
            target=target,
            name=name,
            data_type=Score.DataType.CATEGORICAL,
            value_string=raw_value,
            source=source,
            automated_result=automated_result,
            review=review,
            author=author,
        )

    logger.warning(
        "Score field %s: unsupported value type %s; skipping (v1 only supports bool/int/float/str)",
        name,
        type(raw_value).__name__,
    )
    return None
