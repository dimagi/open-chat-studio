import csv
import io

import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent


@pytest.mark.django_db()
def test_download_dataset_csv_with_context_and_metadata(client, team_with_users):
    """Test CSV download with expanded context."""
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    message1 = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="What is AI?", role="human").model_dump(),
        output=EvaluationMessageContent(content="AI stands for Artificial Intelligence", role="ai").model_dump(),
        context={"current_datetime": "2023-01-01T10:00:00", "user_location": "USA"},
        history=[
            {"message_type": "human", "content": "Hello", "summary": None},
            {"message_type": "ai", "content": "Hi there!", "summary": None},
        ],
    )

    message2 = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Tell me about Python", role="human").model_dump(),
        output=EvaluationMessageContent(content="Python is a programming language", role="ai").model_dump(),
        context={"current_datetime": "2023-01-01T11:00:00", "topic": "programming"},
        history=[],
    )

    dataset.messages.add(message1, message2)

    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_download", args=[team_with_users.slug, dataset.id])
    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    content = response.content.decode("utf-8")
    csv_reader = csv.DictReader(io.StringIO(content))
    rows = list(csv_reader)

    headers = csv_reader.fieldnames
    expected_headers = [
        "id",
        "input_content",
        "output_content",
        "context.current_datetime",
        "context.topic",
        "context.user_location",
        "history",
    ]
    assert headers == expected_headers

    assert len(rows) == 2

    row1 = rows[0]
    assert row1["id"] == str(message1.id)
    assert row1["input_content"] == "What is AI?"
    assert row1["output_content"] == "AI stands for Artificial Intelligence"
    assert row1["context.current_datetime"] == "2023-01-01T10:00:00"
    assert row1["context.user_location"] == "USA"
    assert row1["context.topic"] == ""  # Empty for missing keys
    assert row1["history"] == "user: Hello\nassistant: Hi there!"

    row2 = rows[1]
    assert row2["id"] == str(message2.id)
    assert row2["input_content"] == "Tell me about Python"
    assert row2["output_content"] == "Python is a programming language"
    assert row2["context.current_datetime"] == "2023-01-01T11:00:00"
    assert row2["context.topic"] == "programming"
    assert row2["context.user_location"] == ""  # Empty for missing keys
    assert row2["history"] == ""  # Empty history


@pytest.mark.django_db()
def test_download_empty_dataset_csv(client, team_with_users):
    """Test CSV download for empty dataset returns minimal headers."""
    dataset = EvaluationDataset.objects.create(name="Empty Dataset", team=team_with_users)
    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_download", args=[team_with_users.slug, dataset.id])
    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"

    content = response.content.decode("utf-8")
    csv_reader = csv.reader(io.StringIO(content))
    rows = list(csv_reader)

    assert len(rows) == 1
    assert rows[0] == ["id", "input_content", "output_content", "history"]
