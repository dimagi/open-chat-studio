from unittest.mock import Mock

import pytest

from apps.evaluations.tasks import process_evaluation_results_csv_rows
from apps.evaluations.views.evaluation_config_views import generate_evaluation_results_column_suggestions
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users():
    return TeamWithUsersFactory()


@pytest.fixture()
def evaluation_setup(team_with_users, db):
    """Create a complete evaluation setup with evaluators, run, and results"""
    # Create evaluators
    evaluator1 = EvaluatorFactory(team=team_with_users, name="GPT-4 Evaluator")
    evaluator2 = EvaluatorFactory(team=team_with_users, name="Claude Evaluator")

    # Create dataset with message
    message = EvaluationMessageFactory()
    dataset = EvaluationDatasetFactory(team=team_with_users, messages=[message])

    # Create config with evaluators
    config = EvaluationConfigFactory(team=team_with_users, dataset=dataset)
    config.evaluators.add(evaluator1, evaluator2)

    # Create evaluation run
    run = EvaluationRunFactory(team=team_with_users, config=config)

    # Create evaluation results
    result1 = EvaluationResultFactory(
        team=team_with_users, evaluator=evaluator1, message=message, run=run, output={"result": {"existing_score": 8.5}}
    )
    result2 = EvaluationResultFactory(
        team=team_with_users, evaluator=evaluator2, message=message, run=run, output={"result": {"existing_score": 7.0}}
    )

    return {
        "team": team_with_users,
        "evaluator1": evaluator1,
        "evaluator2": evaluator2,
        "message": message,
        "dataset": dataset,
        "config": config,
        "run": run,
        "result1": result1,
        "result2": result2,
    }


@pytest.mark.django_db()
def test_generate_column_suggestions(evaluation_setup):
    """Test that column suggestions correctly match evaluator names in parentheses"""

    setup = evaluation_setup
    result_columns = [
        "accuracy_score (GPT-4 Evaluator)",
        "relevance_score (Claude Evaluator)",
        "some_other_score",
    ]

    suggestions = generate_evaluation_results_column_suggestions(result_columns, setup["run"])

    assert suggestions["accuracy_score (GPT-4 Evaluator)"] == setup["evaluator1"].id
    assert suggestions["relevance_score (Claude Evaluator)"] == setup["evaluator2"].id
    assert suggestions["some_other_score"] is None

    # Columns that aren't valid evaluators
    result_columns = [
        "random_column",
        "another_column (Unknown Evaluator)",
    ]

    suggestions = generate_evaluation_results_column_suggestions(result_columns, setup["run"])

    assert suggestions["random_column"] is None
    assert suggestions["another_column (Unknown Evaluator)"] is None


@pytest.mark.django_db()
def test_process_csv_with_valid_mapping(evaluation_setup):
    """Test CSV processing with valid evaluator mappings"""
    setup = evaluation_setup

    csv_data = [
        {
            "id": str(setup["message"].id),
            "accuracy_score": "9.0",
            "relevance_score": "8.5",
        }
    ]

    column_mappings = {
        "accuracy_score": setup["evaluator1"].id,
        "relevance_score": setup["evaluator2"].id,
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        setup["run"], csv_data, column_mappings, progress_recorder, setup["team"]
    )

    assert stats["updated_count"] == 2
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 0

    # Verify the updates
    setup["result1"].refresh_from_db()
    setup["result2"].refresh_from_db()

    assert setup["result1"].output["result"]["accuracy_score"] == "9.0"
    assert setup["result2"].output["result"]["relevance_score"] == "8.5"


@pytest.mark.django_db()
def test_process_csv_with_missing_id(evaluation_setup):
    """Test CSV processing with missing ID column"""
    setup = evaluation_setup

    csv_data = [
        {
            "accuracy_score": "9.0",
            # Missing 'id' field
        }
    ]

    column_mappings = {
        "accuracy_score": setup["evaluator1"].id,
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        setup["run"], csv_data, column_mappings, progress_recorder, setup["team"]
    )

    assert stats["updated_count"] == 0
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 1
    assert "Missing 'id' column" in stats["error_messages"][0]


@pytest.mark.django_db()
def test_process_csv_with_invalid_evaluator_id(evaluation_setup):
    """Test CSV processing with invalid evaluator ID"""
    setup = evaluation_setup

    csv_data = [
        {
            "id": str(setup["message"].id),
            "accuracy_score": "9.0",
        }
    ]

    column_mappings = {
        "accuracy_score": 99999,  # Non-existent evaluator ID
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        setup["run"], csv_data, column_mappings, progress_recorder, setup["team"]
    )

    assert stats["updated_count"] == 0
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 1
    assert "Evaluator with ID '99999' not found" in stats["error_messages"][0]


@pytest.mark.django_db()
def test_process_csv_with_non_existent_message(evaluation_setup):
    """Test CSV processing with non-existent message ID"""
    setup = evaluation_setup

    csv_data = [
        {
            "id": "99999",  # Non-existent message ID
            "accuracy_score": "9.0",
        }
    ]

    column_mappings = {
        "accuracy_score": setup["evaluator1"].id,
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        setup["run"], csv_data, column_mappings, progress_recorder, setup["team"]
    )

    assert stats["updated_count"] == 0
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 1
    assert "No evaluation results found for message ID 99999" in stats["error_messages"][0]


@pytest.mark.django_db()
def test_process_csv_no_update_when_value_unchanged(evaluation_setup):
    """Test that no database update occurs when value hasn't changed"""
    setup = evaluation_setup

    setup["result1"].output["result"]["accuracy_score"] = "8.5"
    setup["result1"].save()

    csv_data = [
        {
            "id": str(setup["message"].id),
            "accuracy_score": "8.5",
        }
    ]

    column_mappings = {
        "accuracy_score": setup["evaluator1"].id,
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        setup["run"], csv_data, column_mappings, progress_recorder, setup["team"]
    )

    assert stats["updated_count"] == 0
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 0
