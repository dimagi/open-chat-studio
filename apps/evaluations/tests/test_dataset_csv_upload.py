import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent
from apps.evaluations.tasks import _update_existing_message, process_csv_rows
from apps.evaluations.utils import generate_csv_column_suggestions
from apps.utils.factories.evaluations import EvaluationDatasetFactory
from apps.utils.factories.team import TeamWithUsersFactory


class MockProgressRecorder:
    def set_progress(*args):
        pass


@pytest.fixture()
def team_with_users():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def dataset(team_with_users):
    return EvaluationDatasetFactory(team=team_with_users)


@pytest.fixture()
def client_with_user(team_with_users):
    client = Client()
    client.force_login(team_with_users.members.first())
    return client


@pytest.fixture()
def sample_csv_data():
    """Sample CSV data with conversation history."""
    return [
        {"input": "Hello", "output": "Hi there!", "context_field": "greeting", "date": "2024-01-01"},
        {
            "input": "How are you?",
            "output": "I'm doing well, thanks!",
            "context_field": "wellness",
            "date": "2024-01-01",
        },
        {
            "input": "What's the weather?",
            "output": "I can't check weather directly.",
            "context_field": "weather",
            "date": "2024-01-02",
        },
    ]


@pytest.fixture()
def csv_file_content(sample_csv_data):
    """Create CSV file content from sample data."""
    csv_content = "input,output,context_field,date\n"
    for row in sample_csv_data:
        csv_content += f"{row['input']},{row['output']},{row['context_field']},{row['date']}\n"
    return csv_content


@pytest.mark.django_db()
def test_csv_upload_column_parsing(client_with_user, team_with_users, csv_file_content):
    """Test CSV parsing endpoint returns correct columns and suggestions."""
    csv_file = SimpleUploadedFile("test.csv", csv_file_content.encode(), content_type="text/csv")

    url = reverse("evaluations:parse_csv_columns", args=[team_with_users.slug])
    response = client_with_user.post(url, {"csv_file": csv_file})

    assert response.status_code == 200
    data = response.json()

    # Check columns are detected
    assert "columns" in data
    assert set(data["columns"]) == {"input", "output", "context_field", "date"}

    # Check smart suggestions work
    assert "suggestions" in data
    assert data["suggestions"]["input"] == "input"
    assert data["suggestions"]["output"] == "output"
    assert "context" in data["suggestions"]

    context_suggestions = data["suggestions"]["context"]
    context_field_names = [s["fieldName"] for s in context_suggestions]
    context_csv_columns = [s["csvColumn"] for s in context_suggestions]

    assert "context_field" in context_field_names
    assert "date" in context_field_names
    assert "context_field" in context_csv_columns
    assert "date" in context_csv_columns


@pytest.mark.django_db()
def test_csv_dataset_creation_without_history(client_with_user, team_with_users, sample_csv_data):
    """Test creating dataset from CSV without populate_history."""
    column_mapping = {"input": "input", "output": "output", "context_field": "context_field", "date": "date"}

    form_data = {
        "name": "Test CSV Dataset",
        "mode": "csv",
        "csv_data": json.dumps(sample_csv_data),
        "column_mapping": json.dumps(column_mapping),
        "populate_history": False,
    }

    url = reverse("evaluations:dataset_new", args=[team_with_users.slug])
    response = client_with_user.post(url, form_data)

    assert response.status_code == 302

    dataset = EvaluationDataset.objects.get(name="Test CSV Dataset", team=team_with_users)
    assert dataset.messages.count() == 3

    messages = list(dataset.messages.all().order_by("id"))

    first_message = messages[0]
    assert first_message.input["content"] == "Hello"
    assert first_message.output["content"] == "Hi there!"
    assert first_message.context["context_field"] == "greeting"
    assert first_message.context["date"] == "2024-01-01"
    assert first_message.history == []  # No history when populate_history=False

    second_message = messages[1]
    assert second_message.input["content"] == "How are you?"
    assert second_message.output["content"] == "I'm doing well, thanks!"
    assert second_message.history == []  # No history


@pytest.mark.django_db()
def test_csv_dataset_creation_with_history(client_with_user, team_with_users, sample_csv_data):
    """Test creating dataset from CSV with populate_history enabled."""
    column_mapping = {"input": "input", "output": "output", "context_field": "context_field", "date": "date"}

    form_data = {
        "name": "Test CSV Dataset with History",
        "mode": "csv",
        "csv_data": json.dumps(sample_csv_data),
        "column_mapping": json.dumps(column_mapping),
        "populate_history": True,
    }

    url = reverse("evaluations:dataset_new", args=[team_with_users.slug])
    response = client_with_user.post(url, form_data)

    assert response.status_code == 302

    dataset = EvaluationDataset.objects.get(name="Test CSV Dataset with History", team=team_with_users)
    messages = list(dataset.messages.all().order_by("id"))

    # First message should have empty history
    first_message = messages[0]
    assert first_message.input["content"] == "Hello"
    assert first_message.output["content"] == "Hi there!"
    assert first_message.context["context_field"] == "greeting"
    assert first_message.context["date"] == "2024-01-01"
    assert first_message.history == []

    # Second message should have history from first message
    second_message = messages[1]
    assert second_message.input["content"] == "How are you?"
    assert second_message.output["content"] == "I'm doing well, thanks!"
    assert second_message.context["context_field"] == "wellness"
    assert second_message.context["date"] == "2024-01-01"
    assert len(second_message.history) == 2
    assert second_message.history[0]["message_type"] == "HUMAN"
    assert second_message.history[0]["content"] == "Hello"
    assert second_message.history[1]["message_type"] == "AI"
    assert second_message.history[1]["content"] == "Hi there!"

    # Third message should have history from first two messages
    third_message = messages[2]
    assert third_message.input["content"] == "What's the weather?"
    assert third_message.output["content"] == "I can't check weather directly."
    assert third_message.context["context_field"] == "weather"
    assert third_message.context["date"] == "2024-01-02"
    assert len(third_message.history) == 4
    assert third_message.history[2]["content"] == "How are you?"
    assert third_message.history[3]["content"] == "I'm doing well, thanks!"


def test_csv_column_suggestions_algorithm():
    """Test the column suggestion algorithm directly."""

    columns = ["user_message", "bot_response", "timestamp", "user_id"]
    suggestions = generate_csv_column_suggestions(columns)

    assert suggestions["input"] == "user_message"  # Contains 'user'
    assert suggestions["output"] == "bot_response"  # Contains 'response'
    assert "context" in suggestions

    context_field_names = [s["fieldName"] for s in suggestions["context"]]
    context_csv_columns = [s["csvColumn"] for s in suggestions["context"]]

    assert "timestamp" in context_field_names
    assert "user_id" in context_field_names
    assert "timestamp" in context_csv_columns
    assert "user_id" in context_csv_columns

    # Test with different patterns
    columns2 = ["prompt", "completion", "metadata", "score"]
    suggestions2 = generate_csv_column_suggestions(columns2)

    assert suggestions2["input"] == "prompt"
    assert suggestions2["output"] == "completion"

    context2_field_names = [s["fieldName"] for s in suggestions2["context"]]
    context2_csv_columns = [s["csvColumn"] for s in suggestions2["context"]]

    assert "metadata" in context2_field_names
    assert "score" in context2_field_names
    assert "metadata" in context2_csv_columns
    assert "score" in context2_csv_columns

    # Test ID field filtering
    columns3 = ["id", "input", "output", "context.user_name", "score"]
    suggestions3 = generate_csv_column_suggestions(columns3)

    assert suggestions3["input"] == "input"
    assert suggestions3["output"] == "output"

    context3_field_names = [s["fieldName"] for s in suggestions3["context"]]
    context3_csv_columns = [s["csvColumn"] for s in suggestions3["context"]]

    # ID should be filtered out from context suggestions
    assert "id" not in context3_field_names
    assert "id" not in context3_csv_columns

    # Other fields should be cleaned and included
    assert "user_name" in context3_field_names  # context.user_name -> user_name
    assert "score" in context3_field_names
    assert "context.user_name" in context3_csv_columns  # Original column name preserved
    assert "score" in context3_csv_columns

    # Test history column suggestions
    columns4 = ["id", "input", "output", "history", "context.user_name", "score"]
    suggestions4 = generate_csv_column_suggestions(columns4)

    assert suggestions4["input"] == "input"
    assert suggestions4["output"] == "output"
    assert suggestions4["history"] == "history"  # History should be suggested

    context4_field_names = [s["fieldName"] for s in suggestions4["context"]]
    context4_csv_columns = [s["csvColumn"] for s in suggestions4["context"]]

    # ID and history should be filtered out from context suggestions
    assert "id" not in context4_field_names
    assert "history" not in context4_field_names
    assert "id" not in context4_csv_columns
    assert "history" not in context4_csv_columns

    # Other fields should still be included
    assert "user_name" in context4_field_names
    assert "score" in context4_field_names


@pytest.mark.django_db()
def test_csv_with_empty_rows_handling(client_with_user, team_with_users):
    """Test CSV upload handles empty or invalid rows correctly."""
    csv_data = [
        {"input": "Hello", "output": "Hi!", "context": "valid"},
        {"input": "", "output": "Response", "context": "missing_input"},  # Invalid: empty input
        {"input": "Question", "output": "", "context": "missing_output"},  # Invalid: empty output
        {"input": "Valid", "output": "Valid response", "context": "valid2"},
    ]

    form_data = {
        "name": "Dataset with Empty Rows",
        "mode": "csv",
        "csv_data": json.dumps(csv_data),
        "column_mapping": json.dumps({"input": "input", "output": "output", "context": "context"}),
        "populate_history": False,
    }

    url = reverse("evaluations:dataset_new", args=[team_with_users.slug])
    response = client_with_user.post(url, form_data)

    assert response.status_code == 302

    dataset = EvaluationDataset.objects.get(name="Dataset with Empty Rows", team=team_with_users)
    # Should only create messages for valid rows (rows 1 and 4)
    assert dataset.messages.count() == 2

    messages = list(dataset.messages.all().order_by("id"))
    assert messages[0].input["content"] == "Hello"
    assert messages[1].input["content"] == "Valid"


@pytest.mark.django_db()
def test_csv_dataset_creation_with_generated_history(client_with_user, team_with_users):
    """Test creating dataset from CSV using a history column."""
    csv_data = [
        {
            "input": "What's the weather?",
            "output": "I can't check weather directly.",
            "topic": "weather",
            "history": "user: Hello\nassistant: Hi there!\nuser: How are you?\nassistant: I'm doing well, thanks!",
        },
        {
            "input": "Tell me a joke",
            "output": "Why don't scientists trust atoms? Because they make up everything!",
            "topic": "humor",
            "history": "user: Previous conversation\nassistant: Previous response",
        },
    ]

    column_mapping = {"input": "input", "output": "output", "topic": "topic"}

    form_data = {
        "name": "Test CSV Dataset with History Column",
        "mode": "csv",
        "csv_data": json.dumps(csv_data),
        "column_mapping": json.dumps(column_mapping),
        "populate_history": False,
        "history_column": "history",
    }

    url = reverse("evaluations:dataset_new", args=[team_with_users.slug])
    response = client_with_user.post(url, form_data)

    assert response.status_code == 302

    dataset = EvaluationDataset.objects.get(name="Test CSV Dataset with History Column", team=team_with_users)
    messages = list(dataset.messages.all().order_by("id"))

    # First message should have parsed history
    assert len(messages[0].history) == 4
    assert messages[0].history[0]["message_type"] == "human"
    assert messages[0].history[0]["content"] == "Hello"
    assert messages[0].history[1]["message_type"] == "ai"
    assert messages[0].history[1]["content"] == "Hi there!"
    assert messages[0].history[2]["message_type"] == "human"
    assert messages[0].history[2]["content"] == "How are you?"
    assert messages[0].history[3]["message_type"] == "ai"
    assert messages[0].history[3]["content"] == "I'm doing well, thanks!"

    # Second message should have different parsed history
    assert len(messages[1].history) == 2
    assert messages[1].history[0]["message_type"] == "human"
    assert messages[1].history[0]["content"] == "Previous conversation"
    assert messages[1].history[1]["message_type"] == "ai"
    assert messages[1].history[1]["content"] == "Previous response"

    # Verify context data is still stored correctly
    assert messages[0].context["topic"] == "weather"
    assert messages[1].context["topic"] == "humor"


@pytest.mark.django_db()
def test_update_message_preserves_chat_references_when_content_unchanged(dataset, team_with_users):
    """Test that chat message references are preserved when content doesn't change."""

    chat = Chat.objects.create(team=team_with_users)
    input_chat_msg = ChatMessage.objects.create(chat=chat, content="Test question", message_type=ChatMessageType.HUMAN)
    output_chat_msg = ChatMessage.objects.create(chat=chat, content="Test answer", message_type=ChatMessageType.AI)

    message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Test question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Test answer", role="ai").model_dump(),
        context={"topic": "test"},
        input_chat_message=input_chat_msg,
        expected_output_chat_message=output_chat_msg,
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(message)

    row_data_unchanged = {
        "input_content": "Test question",  # Same as original
        "output_content": "Test answer",  # Same as original
        "context": {"topic": "updated"},
        "history": [],
    }

    _update_existing_message(dataset, message.id, row_data_unchanged, team_with_users)
    message.refresh_from_db()

    assert message.input_chat_message == input_chat_msg
    assert message.expected_output_chat_message == output_chat_msg
    assert message.context == {"topic": "updated"}  # Non-content fields should still update

    row_data_changed = {
        "input_content": "Changed question",  # Different from original
        "output_content": "Test answer",  # Same as original
        "context": {"topic": "changed"},
        "history": [],
    }

    _update_existing_message(dataset, message.id, row_data_changed, team_with_users)
    message.refresh_from_db()

    assert message.input_chat_message is None
    assert message.expected_output_chat_message is not None  # Shouldn't be removed
    assert message.input["content"] == "Changed question"
    assert message.context == {"topic": "changed"}


@pytest.mark.django_db()
def test_update_message_clears_chat_references_when_output_changed(dataset, team_with_users):
    """Test that chat message references are cleared when output content changes."""

    chat = Chat.objects.create(team=team_with_users)
    input_chat_msg = ChatMessage.objects.create(chat=chat, content="Test question", message_type=ChatMessageType.HUMAN)
    output_chat_msg = ChatMessage.objects.create(chat=chat, content="Test answer", message_type=ChatMessageType.AI)

    message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Test question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Test answer", role="ai").model_dump(),
        context={"topic": "test"},
        input_chat_message=input_chat_msg,
        expected_output_chat_message=output_chat_msg,
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(message)

    row_data_changed = {
        "input_content": "Test question",  # Same as original
        "output_content": "Changed answer",  # Different from original
        "context": {"topic": "test"},
        "history": [],
    }

    _update_existing_message(dataset, message.id, row_data_changed, team_with_users)
    message.refresh_from_db()

    assert message.input_chat_message is not None  # Same as before
    assert message.expected_output_chat_message is None
    assert message.output["content"] == "Changed answer"


@pytest.mark.django_db()
def test_csv_upload_creates_row(dataset):
    mock_progress_recorder = MockProgressRecorder()

    columns = [
        "id",
        "input_content",
        "output_content",
        "context.topic",
        "context.difficulty",
        "context.new_field",
        "history",
    ]
    rows = [
        {
            "id": "",
            "input_content": "New question with history",
            "output_content": "New answer",
            "context.topic": "science",
            "context.difficulty": "hard",
            "context.new_field": "added_value",
            "history": "user: Hello\nassistant: Hi there!\nuser: How are you?",
        }
    ]

    stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
    assert stats["created_count"] == 1
    assert stats["updated_count"] == 0
    assert len(stats["error_messages"]) == 0


@pytest.mark.django_db()
def test_csv_upload_updates_row(dataset):
    mock_progress_recorder = MockProgressRecorder()

    existing_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Original question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Original answer", role="ai").model_dump(),
        context={"topic": "tech", "difficulty": "easy", "old_field": "to_be_removed"},
        history=[],
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(existing_message)

    columns = [
        "id",
        "input_content",
        "output_content",
        "context.topic",
        "context.difficulty",
        "context.new_field",
        "history",
    ]
    rows = [
        {
            "id": str(existing_message.id),
            "input_content": "Updated question",
            "output_content": "Updated answer",
            "context.topic": "science",  # changed from "tech"
            "context.difficulty": "medium",  # changed from "easy"
            "context.new_field": "new_added_field",  # new field added
            # Note: "old_field" is missing, so it should be removed
            "history": "user: Previous chat\nassistant: Previous response",
        }
    ]

    stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
    assert stats["created_count"] == 0
    assert stats["updated_count"] == 1
    assert len(stats["error_messages"]) == 0

    existing_message.refresh_from_db()
    assert existing_message.input["content"] == "Updated question"
    assert existing_message.output["content"] == "Updated answer"
    assert existing_message.context == {
        "topic": "science",  # changed
        "difficulty": "medium",  # changed
        "new_field": "new_added_field",  # added
        # "old_field" should be removed
    }
    assert "old_field" not in existing_message.context
    assert len(existing_message.history) == 2
    assert existing_message.history[0]["message_type"] == "human"
    assert existing_message.history[0]["content"] == "Previous chat"


@pytest.mark.django_db()
def test_csv_upload_different_team(dataset):
    mock_progress_recorder = MockProgressRecorder()

    existing_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Original question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Original answer", role="ai").model_dump(),
        context={"topic": "tech", "difficulty": "easy", "old_field": "to_be_removed"},
        history=[],
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(existing_message)

    other_team = TeamWithUsersFactory.create()
    other_dataset = EvaluationDataset.objects.create(name="Other Dataset", team=other_team)
    other_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Other question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Other answer", role="ai").model_dump(),
        context={"topic": "other"},
        metadata={"created_mode": "manual"},
    )
    other_dataset.messages.add(other_message)

    columns = [
        "id",
        "input_content",
        "output_content",
        "context.topic",
        "context.difficulty",
        "context.new_field",
        "history",
    ]
    rows = [
        {
            "id": str(other_message.id),
            "input_content": "Hack attempt",
            "output_content": "Hacked",
            "context.topic": "hacking",
            "history": "user: Bad\nassistant: Very bad",
        }
    ]

    stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
    assert stats["created_count"] == 0
    assert stats["updated_count"] == 0
    assert len(stats["error_messages"]) == 1
    assert stats["error_messages"][0] == f"Row 1: Message with ID {other_message.id} not found"

    # Verify the other team's message was not modified
    other_message.refresh_from_db()
    assert other_message.input["content"] == "Other question"  # unchanged
    assert other_message.output["content"] == "Other answer"  # unchanged
    assert other_message.context == {"topic": "other"}  # unchanged


@pytest.mark.django_db()
def test_csv_upload_context_changes(dataset):
    """Test with only context changes preserve existing chat references"""
    mock_progress_recorder = MockProgressRecorder()

    existing_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Original question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Original answer", role="ai").model_dump(),
        context={"topic": "tech", "difficulty": "easy", "old_field": "to_be_removed"},
        history=[],
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(existing_message)

    columns = [
        "id",
        "input_content",
        "output_content",
        "context.topic",
        "context.difficulty",
        "context.new_field",
        "history",
    ]
    rows = [
        {
            "id": str(existing_message.id),
            "input_content": existing_message.input["content"],  # Same as current (after previous update)
            "output_content": existing_message.output["content"],
            "context.topic": "physics",  # Changed context
            "context.difficulty": "hard",  # Changed context
            "context.new_field": "context_only_change",  # Changed context
            "history": existing_message.full_history,
        }
    ]

    stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
    assert stats["created_count"] == 0
    assert stats["updated_count"] == 1
    assert len(stats["error_messages"]) == 0

    existing_message.refresh_from_db()
    assert existing_message.input["content"] == existing_message.input["content"]
    assert existing_message.output["content"] == existing_message.output["content"]
    assert existing_message.context == {
        "topic": "physics",  # changed
        "difficulty": "hard",  # changed
        "new_field": "context_only_change",  # changed
    }
    assert "old_field" not in existing_message.context
