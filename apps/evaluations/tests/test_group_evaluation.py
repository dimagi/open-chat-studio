from typing import cast
from unittest.mock import patch

import pytest

from apps.evaluations.models import EvaluationConfig, EvaluationRunStatus, EvaluationRunType
from apps.evaluations.tests.coordination import sweep
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory.create()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory.create(name="gpt-4o")


@pytest.mark.django_db()
def test_group_evaluation_with_multiple_evaluators():
    """Test that evaluation is set up correctly with multiple evaluators"""
    evaluation_message = EvaluationMessageFactory.create(
        input={"content": "Test message", "role": "human"},
        output={"content": "Test response", "role": "ai"},
        create_chat_messages=True,
    )

    # Create 3 evaluators
    evaluator1 = EvaluatorFactory.create(type="LlmEvaluator")
    evaluator2 = EvaluatorFactory.create(type="LlmEvaluator")
    evaluator3 = EvaluatorFactory.create(type="LlmEvaluator")

    dataset = EvaluationDatasetFactory.create(messages=[evaluation_message])
    evaluation_config = cast(
        EvaluationConfig,
        EvaluationConfigFactory.create(evaluators=[evaluator1, evaluator2, evaluator3], dataset=dataset),
    )

    evaluation_run = evaluation_config.run()

    # Check that the evaluation run was created properly
    evaluation_run.refresh_from_db()
    assert evaluation_run.status == EvaluationRunStatus.PENDING

    # Verify config has expected number of evaluators and messages
    assert evaluation_config.evaluators.count() == 3
    assert evaluation_config.dataset.messages.count() == 1


@pytest.mark.django_db()
def test_empty_evaluation_config():
    """Test that empty evaluation config is handled correctly"""
    # Create config with no evaluators
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory.create(evaluators=[]))

    evaluation_run = evaluation_config.run()

    # A run is still created; it stays PENDING until the coordinator picks it up.
    evaluation_run.refresh_from_db()
    assert evaluation_run.status == EvaluationRunStatus.PENDING


@pytest.mark.django_db()
@patch("apps.evaluations.tasks._publish_tick")
@patch("apps.evaluations.tasks.evaluate_message_batch.delay")
def test_sweep_marks_run_complete_when_all_results_present(delay_mock, _publish, team_with_users):
    config = EvaluationConfigFactory.create(team=team_with_users)
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config.evaluators.set([evaluator])
    message = EvaluationMessageFactory.create()
    config.dataset.messages.add(message)
    run = EvaluationRunFactory.create(
        config=config,
        team=team_with_users,
        type=EvaluationRunType.FULL,
        status=EvaluationRunStatus.PROCESSING,
        evaluator_ids=[evaluator.id],
        in_flight=[message.id],
    )
    run.scoped_messages.add(message)
    EvaluationResultFactory.create(
        team=team_with_users, run=run, evaluator=evaluator, message=message, output={"result": {"ok": 1}}
    )

    sweep()

    run.refresh_from_db()
    assert run.status == EvaluationRunStatus.COMPLETED
    assert run.finished_at is not None
