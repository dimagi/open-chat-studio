from typing import cast
from unittest.mock import Mock, patch

import pytest

from apps.evaluations.models import EvaluationConfig, EvaluationRunStatus
from apps.evaluations.tasks import mark_evaluation_complete
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory(name="gpt-4o")


@pytest.mark.django_db()
def test_group_evaluation_with_multiple_evaluators():
    """Test that evaluation is set up correctly with multiple evaluators"""
    evaluation_message = EvaluationMessageFactory(
        input={"content": "Test message", "role": "human"},
        output={"content": "Test response", "role": "ai"},
        create_chat_messages=True,
    )

    # Create 3 evaluators
    evaluator1 = EvaluatorFactory(type="LlmEvaluator")
    evaluator2 = EvaluatorFactory(type="LlmEvaluator")
    evaluator3 = EvaluatorFactory(type="LlmEvaluator")

    dataset = EvaluationDatasetFactory(messages=[evaluation_message])
    evaluation_config = cast(
        EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator1, evaluator2, evaluator3], dataset=dataset)
    )

    # Mock the main task
    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_task:
        mock_result = Mock()
        mock_result.id = "test-task-id"
        mock_task.return_value = mock_result

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
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[]))

    # Mock the main task to see what happens
    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_task:
        mock_result = Mock()
        mock_result.id = "test-task-id"
        mock_task.return_value = mock_result

        evaluation_run = evaluation_config.run()

        # The task should still be called, even with no evaluators
        evaluation_run.refresh_from_db()
        assert evaluation_run.status == EvaluationRunStatus.PENDING

        # Task should be called with the evaluation run id
        mock_task.assert_called_once_with(evaluation_run.id)


@pytest.mark.django_db()
def test_chord_completion_callback(team_with_users):
    """Test that the chord completion callback correctly marks evaluation as complete"""
    evaluation_run = EvaluationRunFactory(team=team_with_users, status=EvaluationRunStatus.PROCESSING)

    # Call the completion callback
    mark_evaluation_complete([], evaluation_run.id)

    # Check that the evaluation run was marked as complete
    evaluation_run.refresh_from_db()
    assert evaluation_run.status == EvaluationRunStatus.COMPLETED
    assert evaluation_run.finished_at is not None


@pytest.mark.django_db()
def test_chord_completion_callback_already_complete(team_with_users):
    """Test that the callback doesn't change already completed runs"""
    evaluation_run = EvaluationRunFactory(
        team=team_with_users,
        status=EvaluationRunStatus.COMPLETED,  # Already completed
    )
    original_finished_at = evaluation_run.finished_at

    # Call the completion callback
    mark_evaluation_complete([], evaluation_run.id)

    # Check that nothing changed
    evaluation_run.refresh_from_db()
    assert evaluation_run.status == EvaluationRunStatus.COMPLETED
    assert evaluation_run.finished_at == original_finished_at
