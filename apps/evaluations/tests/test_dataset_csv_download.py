import csv
import io

import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent
from apps.utils.factories.evaluations import EvaluationMessageFactory


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


@pytest.mark.django_db()
def test_download_dataset_csv_with_participant_data_and_session_state(client, team_with_users):
    """Test CSV download with participant_data and session_state expansion."""
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    # Test case 1: Message with both participant_data and session_state
    message1 = EvaluationMessageFactory(
        input=EvaluationMessageContent(content="What is AI?", role="human").model_dump(),
        output=EvaluationMessageContent(content="AI stands for Artificial Intelligence", role="ai").model_dump(),
        context={"current_datetime": "2023-01-01T10:00:00"},
        participant_data={"age": "25", "name": "John"},
        session_state={"step": "1", "completed": "false"},
        history=[],
    )

    # Test case 2: Message with only participant_data
    message2 = EvaluationMessageFactory(
        input=EvaluationMessageContent(content="Tell me about Python", role="human").model_dump(),
        output=EvaluationMessageContent(content="Python is a programming language", role="ai").model_dump(),
        context={"topic": "programming"},
        participant_data={"age": "30", "location": "USA"},
        session_state={},
        history=[],
    )

    # Test case 3: Message with only session_state
    message3 = EvaluationMessageFactory(
        input=EvaluationMessageContent(content="What is Django?", role="human").model_dump(),
        output=EvaluationMessageContent(content="Django is a web framework", role="ai").model_dump(),
        context={},
        participant_data={},
        session_state={"step": "2", "last_action": "query"},
        history=[],
    )

    # Test case 4: Message with neither
    message4 = EvaluationMessageFactory(
        input=EvaluationMessageContent(content="Hello", role="human").model_dump(),
        output=EvaluationMessageContent(content="Hi there!", role="ai").model_dump(),
        context={},
        participant_data={},
        session_state={},
        history=[],
    )

    # Test case 5: Message with nested JSON structures
    message5 = EvaluationMessageFactory(
        input=EvaluationMessageContent(content="Complex data", role="human").model_dump(),
        output=EvaluationMessageContent(content="Understood", role="ai").model_dump(),
        context={"nested": {"foo": {"bar": [1, 2, "3"]}}},
        participant_data={"preferences": {"theme": "dark", "notifications": {"email": True, "sms": False}}},
        session_state={"workflow": {"steps": ["start", "middle", "end"], "current": 1}},
        history=[],
    )

    dataset.messages.add(message1, message2, message3, message4, message5)

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
        "context.nested",
        "context.topic",
        "participant_data.age",
        "participant_data.location",
        "participant_data.name",
        "participant_data.preferences",
        "session_state.completed",
        "session_state.last_action",
        "session_state.step",
        "session_state.workflow",
        "history",
    ]
    assert headers == expected_headers

    assert len(rows) == 5

    # Verify row 1: has all participant_data and session_state keys
    row1 = rows[0]
    assert row1["id"] == str(message1.id)
    assert row1["input_content"] == "What is AI?"
    assert row1["context.current_datetime"] == "2023-01-01T10:00:00"
    assert row1["participant_data.age"] == "25"
    assert row1["participant_data.name"] == "John"
    assert row1["participant_data.location"] == ""  # Empty for missing keys
    assert row1["session_state.step"] == "1"
    assert row1["session_state.completed"] == "false"
    assert row1["session_state.last_action"] == ""  # Empty for missing keys

    # Verify row 2: has partial participant_data, no session_state
    row2 = rows[1]
    assert row2["id"] == str(message2.id)
    assert row2["participant_data.age"] == "30"
    assert row2["participant_data.location"] == "USA"
    assert row2["participant_data.name"] == ""  # Empty for missing keys
    assert row2["session_state.step"] == ""
    assert row2["session_state.completed"] == ""
    assert row2["session_state.last_action"] == ""

    # Verify row 3: no participant_data, has session_state
    row3 = rows[2]
    assert row3["id"] == str(message3.id)
    assert row3["participant_data.age"] == ""
    assert row3["participant_data.location"] == ""
    assert row3["participant_data.name"] == ""
    assert row3["session_state.step"] == "2"
    assert row3["session_state.last_action"] == "query"
    assert row3["session_state.completed"] == ""

    # Verify row 4: no participant_data or session_state
    row4 = rows[3]
    assert row4["id"] == str(message4.id)
    assert row4["participant_data.age"] == ""
    assert row4["participant_data.location"] == ""
    assert row4["participant_data.name"] == ""
    assert row4["session_state.step"] == ""
    assert row4["session_state.completed"] == ""
    assert row4["session_state.last_action"] == ""

    # Verify row 5: nested JSON structures are serialized as valid JSON strings
    row5 = rows[4]
    assert row5["id"] == str(message5.id)
    assert row5["input_content"] == "Complex data"
    # Nested structures should be serialized as JSON strings
    import json

    assert json.loads(row5["context.nested"]) == {"foo": {"bar": [1, 2, "3"]}}
    assert json.loads(row5["participant_data.preferences"]) == {
        "theme": "dark",
        "notifications": {"email": True, "sms": False},
    }
    assert json.loads(row5["session_state.workflow"]) == {"steps": ["start", "middle", "end"], "current": 1}
