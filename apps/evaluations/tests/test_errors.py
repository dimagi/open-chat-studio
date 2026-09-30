import anthropic
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from apps.evaluations.errors import (
    EvaluationErrorCategory,
    classify_evaluation_error,
    evaluator_error_output,
    is_failed_output,
)
from apps.service_providers.llm_service.error_classification import ProviderErrorKind
from apps.service_providers.llm_service.structured_output import NoStructuredOutputError
from apps.utils.tests.provider_errors import converted, status_error

QUOTA_MESSAGE = "You have reached your specified API usage limits."


@pytest.mark.parametrize(
    ("error", "category"),
    [
        pytest.param(
            converted(status_error(anthropic.BadRequestError, 400, QUOTA_MESSAGE)),
            EvaluationErrorCategory.QUOTA_EXHAUSTED,
            id="provider-error",
        ),
        pytest.param(SoftTimeLimitExceeded(), EvaluationErrorCategory.TIMEOUT, id="celery-soft-time-limit"),
        pytest.param(
            NoStructuredOutputError(reason="max_tokens"),
            EvaluationErrorCategory.INVALID_OUTPUT,
            id="no-structured-output",
        ),
        pytest.param(ValueError("boom"), EvaluationErrorCategory.OTHER, id="unknown"),
    ],
)
def test_classify_evaluation_error(error, category):
    assert classify_evaluation_error(error) == category


@pytest.mark.parametrize("kind", list(ProviderErrorKind))
def test_every_provider_error_kind_has_a_category(kind):
    """A kind added without a matching category would only ever show as "Other error"."""
    assert kind.value in EvaluationErrorCategory.values


def test_an_error_without_a_message_is_recorded_by_its_type():
    assert evaluator_error_output(ValueError()) == {"error": "ValueError", "error_category": "other"}


@pytest.mark.parametrize(
    ("output", "failed"),
    [
        pytest.param({"result": {"score": 1}}, False, id="success"),
        pytest.param({"error": "boom"}, True, id="evaluator-error"),
        pytest.param({"result": {"score": 1}, "generation_error": "boom"}, True, id="generation-error"),
        pytest.param({}, False, id="empty"),
        pytest.param(None, False, id="none"),
    ],
)
def test_is_failed_output(output, failed):
    assert is_failed_output(output) is failed
