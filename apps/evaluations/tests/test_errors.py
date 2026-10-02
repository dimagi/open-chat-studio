import anthropic
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from apps.evaluations.errors import (
    FAILED_OUTPUT_Q,
    EvaluationErrorCategory,
    EvaluationErrorSource,
    MessageError,
    RunErrorSummary,
    classify_evaluation_error,
    evaluator_error_output,
    is_failed_output,
    message_errors,
)
from apps.service_providers.llm_service.error_classification import ProviderErrorKind
from apps.service_providers.llm_service.structured_output import NoStructuredOutputError
from apps.utils.factories.evaluations import EvaluationResultFactory, EvaluationRunFactory, EvaluatorFactory
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
        pytest.param({"error": None}, True, id="error-stored-as-null"),
        pytest.param({}, False, id="empty"),
    ],
)
@pytest.mark.django_db()
def test_is_failed_output_matches_the_database_filter(output, failed):
    """The table's badges use the Python check and the run summary the filter, so the two must agree."""
    run = EvaluationRunFactory.create()
    EvaluationResultFactory.create(run=run, team=run.team, output=output)

    assert is_failed_output(output) is failed
    assert run.results.filter(FAILED_OUTPUT_Q).exists() is failed


def test_a_missing_output_has_not_failed():
    assert is_failed_output(None) is False


@pytest.mark.parametrize(
    ("failed", "total", "warn"),
    [
        pytest.param(0, 312, False, id="no-errors"),
        pytest.param(26, 312, True, id="the-run-from-the-issue"),
        pytest.param(1, 20, False, id="rate-exactly-five-percent"),
        pytest.param(0, 0, False, id="empty-run"),
    ],
)
def test_needs_warning(failed, total, warn):
    assert RunErrorSummary(failed_count=failed, total_count=total, groups=[]).needs_warning is warn


@pytest.mark.parametrize("category", list(EvaluationErrorCategory))
def test_every_category_has_a_hint(category):
    """The run page reads the hint for every failure it shows, so a missing one would fail the page."""
    assert category.hint


GENERATION_FAILED = {"generation_error": "quota", "generation_error_category": "quota_exhausted"}


def test_message_errors_lists_a_shared_generation_failure_once():
    errors = message_errors(
        [
            ("Judge A", {"result": {"score": 0}, **GENERATION_FAILED}),
            ("Judge B", {"error": "429", "error_category": "rate_limit", **GENERATION_FAILED}),
        ]
    )

    assert errors == [
        MessageError(
            source=EvaluationErrorSource.GENERATION,
            category=EvaluationErrorCategory.QUOTA_EXHAUSTED,
            message="quota",
        ),
        MessageError(
            source=EvaluationErrorSource.EVALUATOR,
            category=EvaluationErrorCategory.RATE_LIMIT,
            message="429",
            evaluator_name="Judge B",
        ),
    ]


def test_message_errors_without_a_generation_failure_keeps_each_evaluator():
    errors = message_errors([("Judge A", {"error": "a"}), ("Judge B", {"result": {}}), ("Judge C", {"error": "c"})])

    assert [(error.evaluator_name, error.message) for error in errors] == [("Judge A", "a"), ("Judge C", "c")]


@pytest.mark.django_db()
class TestErrorSummary:
    @pytest.fixture()
    def run(self):
        return EvaluationRunFactory.create()

    def _result(self, run, output, evaluator=None):
        return EvaluationResultFactory.create(
            run=run, team=run.team, output=output, evaluator=evaluator or EvaluatorFactory.create(team=run.team)
        )

    def test_counts_failures_by_source_and_category(self, run):
        evaluator = EvaluatorFactory.create(team=run.team)
        self._result(run, {"result": {"score": 1}}, evaluator)
        for _ in range(2):
            self._result(run, {"result": {"score": 0}, **GENERATION_FAILED}, evaluator)
        self._result(run, {"error": "429", "error_category": "rate_limit"}, evaluator)

        summary = run.error_summary()

        assert summary.failed_count == 3
        assert summary.total_count == 4
        assert [(group.source, group.category, group.count) for group in summary.groups] == [
            (EvaluationErrorSource.GENERATION, EvaluationErrorCategory.QUOTA_EXHAUSTED, 2),
            (EvaluationErrorSource.EVALUATOR, EvaluationErrorCategory.RATE_LIMIT, 1),
        ]
        assert summary.needs_warning is True

    def test_error_without_category_counts_as_other(self, run):
        """Results written before categories existed carry only the message."""
        self._result(run, {"error": "boom"})

        summary = run.error_summary()

        assert [(group.category, group.count) for group in summary.groups] == [(EvaluationErrorCategory.OTHER, 1)]

    def test_generation_failure_is_the_source_when_the_evaluator_also_failed(self, run):
        self._result(run, {"error": "judge failed", **GENERATION_FAILED})

        (group,) = run.error_summary().groups

        assert group.source == EvaluationErrorSource.GENERATION

    def test_an_error_stored_as_null_still_counts(self, run):
        self._result(run, {"error": None})

        summary = run.error_summary()

        assert summary.failed_count == 1
        assert [(group.source, group.category) for group in summary.groups] == [
            (EvaluationErrorSource.EVALUATOR, EvaluationErrorCategory.OTHER)
        ]
