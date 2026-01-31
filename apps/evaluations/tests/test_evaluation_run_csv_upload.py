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


@pytest.fixture()
def comprehensive_evaluation_dataset(team_with_users, db):
    """Create a comprehensive evaluation dataset with multiple messages and varied data types"""
    # Create evaluators with different output schemas
    accuracy_evaluator = EvaluatorFactory(
        team=team_with_users,
        name="Accuracy Evaluator",
        params={
            "output_schema": {
                "accuracy": {"type": "float", "description": "Accuracy score 0-1"},
                "confidence": {"type": "float", "description": "Confidence level"},
            }
        },
    )

    quality_evaluator = EvaluatorFactory(
        team=team_with_users,
        name="Quality Evaluator",
        params={
            "output_schema": {
                "quality_score": {"type": "int", "description": "Quality rating 1-5"},
                "category": {"type": "string", "description": "Quality category"},
            }
        },
    )

    sentiment_evaluator = EvaluatorFactory(
        team=team_with_users,
        name="Sentiment Evaluator",
        params={
            "output_schema": {
                "sentiment": {"type": "string", "description": "Sentiment classification"},
                "polarity": {"type": "float", "description": "Polarity score -1 to 1"},
            }
        },
    )

    # Create multiple messages with varied content
    messages = [
        EvaluationMessageFactory(
            input={"content": "What is the capital of France?", "role": "human"},
            output={"content": "The capital of France is Paris.", "role": "ai"},
        ),
        EvaluationMessageFactory(
            input={"content": "Explain quantum computing", "role": "human"},
            output={"content": "Quantum computing uses quantum mechanics principles...", "role": "ai"},
        ),
        EvaluationMessageFactory(
            input={"content": "How do I bake a cake?", "role": "human"},
            output={"content": "To bake a cake, you need flour, eggs, sugar...", "role": "ai"},
        ),
        EvaluationMessageFactory(
            input={"content": "What's the weather like today?", "role": "human"},
            output={"content": "I don't have access to real-time weather data.", "role": "ai"},
        ),
        EvaluationMessageFactory(
            input={"content": "Tell me a joke", "role": "human"},
            output={"content": "Why don't scientists trust atoms? Because they make up everything!", "role": "ai"},
        ),
    ]

    # Create dataset with all messages
    dataset = EvaluationDatasetFactory(team=team_with_users, messages=messages)

    # Create config with all evaluators
    config = EvaluationConfigFactory(team=team_with_users, dataset=dataset, name="Comprehensive Evaluation")
    config.evaluators.add(accuracy_evaluator, quality_evaluator, sentiment_evaluator)

    # Create evaluation run
    run = EvaluationRunFactory(team=team_with_users, config=config)

    # Create results with varied scores and categories
    results = []

    # Message 1: High accuracy factual answer
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=accuracy_evaluator,
            message=messages[0],
            run=run,
            output={"result": {"accuracy": 0.95, "confidence": 0.98}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=quality_evaluator,
            message=messages[0],
            run=run,
            output={"result": {"quality_score": 5, "category": "excellent"}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=sentiment_evaluator,
            message=messages[0],
            run=run,
            output={"result": {"sentiment": "neutral", "polarity": 0.1}},
        )
    )

    # Message 2: Good technical explanation
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=accuracy_evaluator,
            message=messages[1],
            run=run,
            output={"result": {"accuracy": 0.85, "confidence": 0.75}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=quality_evaluator,
            message=messages[1],
            run=run,
            output={"result": {"quality_score": 4, "category": "good"}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=sentiment_evaluator,
            message=messages[1],
            run=run,
            output={"result": {"sentiment": "neutral", "polarity": 0.05}},
        )
    )

    # Message 3: Average practical answer
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=accuracy_evaluator,
            message=messages[2],
            run=run,
            output={"result": {"accuracy": 0.78, "confidence": 0.82}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=quality_evaluator,
            message=messages[2],
            run=run,
            output={"result": {"quality_score": 3, "category": "average"}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=sentiment_evaluator,
            message=messages[2],
            run=run,
            output={"result": {"sentiment": "positive", "polarity": 0.4}},
        )
    )

    # Message 4: Lower quality - can't answer
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=accuracy_evaluator,
            message=messages[3],
            run=run,
            output={"result": {"accuracy": 0.6, "confidence": 0.9}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=quality_evaluator,
            message=messages[3],
            run=run,
            output={"result": {"quality_score": 2, "category": "poor"}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=sentiment_evaluator,
            message=messages[3],
            run=run,
            output={"result": {"sentiment": "negative", "polarity": -0.2}},
        )
    )

    # Message 5: Creative response
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=accuracy_evaluator,
            message=messages[4],
            run=run,
            output={"result": {"accuracy": 0.88, "confidence": 0.85}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=quality_evaluator,
            message=messages[4],
            run=run,
            output={"result": {"quality_score": 4, "category": "good"}},
        )
    )
    results.append(
        EvaluationResultFactory(
            team=team_with_users,
            evaluator=sentiment_evaluator,
            message=messages[4],
            run=run,
            output={"result": {"sentiment": "positive", "polarity": 0.75}},
        )
    )

    return {
        "team": team_with_users,
        "evaluators": {
            "accuracy": accuracy_evaluator,
            "quality": quality_evaluator,
            "sentiment": sentiment_evaluator,
        },
        "messages": messages,
        "dataset": dataset,
        "config": config,
        "run": run,
        "results": results,
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

    assert setup["result1"].output["result"]["accuracy_score"] == 9.0
    assert setup["result2"].output["result"]["relevance_score"] == 8.5


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

    # Store as float (as it would be after CSV conversion)
    setup["result1"].output["result"]["accuracy_score"] = 8.5
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


@pytest.mark.django_db()
def test_bulk_csv_update_with_comprehensive_dataset(comprehensive_evaluation_dataset):
    """Test bulk CSV updates across multiple messages and evaluators with aggregate verification"""
    from apps.evaluations.aggregation import compute_aggregates_for_run
    from apps.evaluations.models import EvaluationRunAggregate

    dataset = comprehensive_evaluation_dataset

    # Compute initial aggregates
    compute_aggregates_for_run(dataset["run"])

    # Verify initial aggregates for accuracy evaluator
    accuracy_agg = EvaluationRunAggregate.objects.get(run=dataset["run"], evaluator=dataset["evaluators"]["accuracy"])
    initial_accuracy_mean = accuracy_agg.aggregates["accuracy"]["mean"]
    # Mean of [0.95, 0.85, 0.78, 0.6, 0.88] = 0.812
    assert initial_accuracy_mean == 0.812

    # Verify initial aggregates for quality evaluator
    quality_agg = EvaluationRunAggregate.objects.get(run=dataset["run"], evaluator=dataset["evaluators"]["quality"])
    initial_quality_mode = quality_agg.aggregates["category"]["mode"]
    # Mode of ["excellent", "good", "average", "poor", "good"] = "good"
    assert initial_quality_mode == "good"

    # Create CSV data to bulk update scores
    csv_data = [
        {
            "id": str(dataset["messages"][0].id),
            "accuracy (Accuracy Evaluator)": "0.92",  # decreased from 0.95
            "quality_score (Quality Evaluator)": "4",  # decreased from 5
        },
        {
            "id": str(dataset["messages"][1].id),
            "accuracy (Accuracy Evaluator)": "0.90",  # increased from 0.85
            "quality_score (Quality Evaluator)": "5",  # increased from 4
        },
        {
            "id": str(dataset["messages"][2].id),
            "accuracy (Accuracy Evaluator)": "0.80",  # increased from 0.78
            "quality_score (Quality Evaluator)": "4",  # increased from 3
        },
        {
            "id": str(dataset["messages"][3].id),
            "accuracy (Accuracy Evaluator)": "0.70",  # increased from 0.6
            "quality_score (Quality Evaluator)": "3",  # increased from 2
        },
        {
            "id": str(dataset["messages"][4].id),
            "accuracy (Accuracy Evaluator)": "0.85",  # decreased from 0.88
            "quality_score (Quality Evaluator)": "5",  # increased from 4
        },
    ]

    column_mappings = {
        "accuracy (Accuracy Evaluator)": dataset["evaluators"]["accuracy"].id,
        "quality_score (Quality Evaluator)": dataset["evaluators"]["quality"].id,
    }

    progress_recorder = Mock()
    stats = process_evaluation_results_csv_rows(
        dataset["run"], csv_data, column_mappings, progress_recorder, dataset["team"]
    )

    # Verify all updates succeeded
    assert stats["updated_count"] == 10  # 5 messages × 2 columns
    assert stats["created_count"] == 0
    assert len(stats["error_messages"]) == 0

    # Recompute aggregates after updates
    compute_aggregates_for_run(dataset["run"])

    # Verify updated aggregates
    accuracy_agg.refresh_from_db()
    updated_accuracy_mean = accuracy_agg.aggregates["accuracy"]["mean"]
    # Mean of [0.92, 0.90, 0.80, 0.70, 0.85] = 0.834
    assert updated_accuracy_mean == 0.834

    quality_agg.refresh_from_db()
    # quality_score is numeric (int), so check mean instead of distribution
    updated_quality_mean = quality_agg.aggregates["quality_score"]["mean"]
    # Mean of [4, 5, 4, 3, 5] = 4.2
    assert updated_quality_mean == 4.2

    # Check categorical field (category) for distribution
    updated_category_distribution = quality_agg.aggregates["category"]["distribution"]
    # Categories weren't updated, so should match initial state
    assert "excellent" in updated_category_distribution
    assert "good" in updated_category_distribution


@pytest.mark.django_db()
def test_upload_task_recomputes_aggregates(evaluation_setup):
    """Test that uploading CSV results triggers aggregate recalculation"""
    from apps.evaluations.aggregation import compute_aggregates_for_run
    from apps.evaluations.models import EvaluationRunAggregate

    setup = evaluation_setup

    # Initial results with score 8.5 and 7.0
    setup["result1"].output = {"result": {"score": 8.5}}
    setup["result1"].save()
    setup["result2"].output = {"result": {"score": 7.0}}
    setup["result2"].save()

    # Compute initial aggregates
    compute_aggregates_for_run(setup["run"])

    # Verify initial aggregates
    agg1 = EvaluationRunAggregate.objects.get(run=setup["run"], evaluator=setup["evaluator1"])
    assert agg1.aggregates["score"]["mean"] == 8.5

    agg2 = EvaluationRunAggregate.objects.get(run=setup["run"], evaluator=setup["evaluator2"])
    assert agg2.aggregates["score"]["mean"] == 7.0

    # Update results via CSV upload
    csv_data = [
        {
            "id": str(setup["message"].id),
            "new_score (GPT-4 Evaluator)": "9.5",
            "new_score (Claude Evaluator)": "8.0",
        }
    ]

    column_mappings = {
        "new_score (GPT-4 Evaluator)": setup["evaluator1"].id,
        "new_score (Claude Evaluator)": setup["evaluator2"].id,
    }

    progress_recorder = Mock()
    process_evaluation_results_csv_rows(setup["run"], csv_data, column_mappings, progress_recorder, setup["team"])

    # Manually trigger aggregation (simulating what the task does)
    compute_aggregates_for_run(setup["run"])

    # Verify aggregates were updated with new values
    agg1.refresh_from_db()
    agg2.refresh_from_db()

    assert agg1.aggregates["new_score"]["mean"] == 9.5
    assert agg2.aggregates["new_score"]["mean"] == 8.0
