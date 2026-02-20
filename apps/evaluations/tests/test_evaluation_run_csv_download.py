import csv
import io

import pytest
from django.urls import reverse

from apps.evaluations.evaluators import EvaluatorResult
from apps.utils.factories.evaluations import (
    EvaluationResultFactory,
)


@pytest.mark.django_db()
def test_download_evaluation_run_csv_with_different_context_columns(client, team_with_users):
    """Test CSV download includes all context columns even when messages have different context keys."""

    evaluator_result1 = EvaluatorResult(
        message={
            "input": {"content": "What is AI?", "role": "human"},
            "output": {"content": "AI stands for Artificial Intelligence", "role": "ai"},
            "context": {"user_location": "USA", "user_age": "25"},
            "history": [],
            "metadata": {},
        },
        result={"score": 8.5, "accuracy": 0.9},
        generated_response="Generated AI response",
    )

    evaluator_result2 = EvaluatorResult(
        message={
            "input": {"content": "Tell me about Python", "role": "human"},
            "output": {"content": "Python is a programming language", "role": "ai"},
            "context": {"topic": "programming", "difficulty": "beginner"},
            "history": [],
            "metadata": {},
        },
        result={"score": 7.0, "helpfulness": 0.8},
        generated_response="Generated Python response",
    )

    result1 = EvaluationResultFactory(output=evaluator_result1.model_dump(), team=team_with_users)
    result1.run.team = team_with_users  # ty: ignore[invalid-assignment]
    result1.run.save()

    EvaluationResultFactory(
        output=evaluator_result2.model_dump(), team=team_with_users, run=result1.run, evaluator=result1.evaluator
    )

    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse(
        "evaluations:evaluation_run_download", args=[team_with_users.slug, result1.run.config.id, result1.run.id]
    )

    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    content = response.content.decode("utf-8")
    csv_reader = csv.DictReader(io.StringIO(content))
    rows = list(csv_reader)

    headers = csv_reader.fieldnames
    assert headers is not None

    # Check that all context columns are present even though messages have different keys
    expected_context_columns = ["difficulty", "topic", "user_age", "user_location"]
    for context_col in expected_context_columns:
        assert context_col in headers, f"Missing context column: {context_col}"

    # Check that all evaluator result columns are present
    evaluator_name = result1.evaluator.name
    expected_evaluator_columns = [
        f"accuracy ({evaluator_name})",
        f"helpfulness ({evaluator_name})",
        f"score ({evaluator_name})",
    ]
    for eval_col in expected_evaluator_columns:
        assert eval_col in headers, f"Missing evaluator column: {eval_col}"

    assert len(rows) == 2

    row1 = rows[0]
    assert row1["user_location"] == "USA"
    assert row1["user_age"] == "25"
    assert row1["topic"] == ""  # Empty for missing key
    assert row1["difficulty"] == ""  # Empty for missing key
    assert row1[f"score ({evaluator_name})"] == "8.5"
    assert row1[f"accuracy ({evaluator_name})"] == "0.9"
    assert row1[f"helpfulness ({evaluator_name})"] == ""  # Empty for missing key

    row2 = rows[1]
    assert row2["topic"] == "programming"
    assert row2["difficulty"] == "beginner"
    assert row2["user_location"] == ""  # Empty for missing key
    assert row2["user_age"] == ""  # Empty for missing key
    assert row2[f"score ({evaluator_name})"] == "7.0"
    assert row2[f"helpfulness ({evaluator_name})"] == "0.8"
    assert row2[f"accuracy ({evaluator_name})"] == ""  # Empty for missing key
