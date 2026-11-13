import pytest

from apps.evaluations.models import EvaluationRunStatus
from apps.evaluations.notifications import (
    auto_population_rule_disabled_notification,
    evaluation_run_outcome_notification,
)
from apps.ocs_notifications.tests.rendering_helpers import rendered_message
from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory, EvaluationRunFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(
            lambda: auto_population_rule_disabled_notification(
                DatasetAutoPopulationRuleFactory.create(
                    dataset__name="Weather *beta*", source_experiment__name="__init__ bot"
                ),
                reason="KeyError: '__init__'",
            ),
            "The auto-population rule for dataset 'Weather *beta*' (source: __init__ bot) was automatically "
            "disabled: KeyError: '__init__'.",
            id="auto-population-rule-disabled",
        ),
        pytest.param(
            lambda: evaluation_run_outcome_notification(
                EvaluationRunFactory.create(status=EvaluationRunStatus.FAILED, error_message="KeyError: '__init__'")
            ),
            "The evaluation run stopped before it finished: KeyError: '__init__'",
            id="evaluation-run-failed",
        ),
    ],
)
def test_runtime_values_render_as_written(build, expected):
    """Names and error text are data, not markdown, so the rendered message shows them exactly."""
    assert rendered_message("apps.evaluations.notifications.create_notification", build) == expected
