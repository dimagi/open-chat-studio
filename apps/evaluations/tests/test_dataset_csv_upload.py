import csv
import json
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent
from apps.evaluations.tasks import _update_existing_message, process_csv_rows
from apps.evaluations.utils import generate_csv_column_suggestions
from apps.files.models import File, FilePurpose
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
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["input", "output", "context_field", "date"])
    writer.writeheader()
    writer.writerows(sample_csv_data)
    return output.getvalue()


@pytest.fixture()
def csv_file_instance(team_with_users, csv_file_content):
    """Create a File instance with CSV content."""
    csv_file = SimpleUploadedFile("test.csv", csv_file_content.encode(), content_type="text/csv")
    return File.create(
        filename="test.csv",
        file_obj=csv_file,
        team_id=team_with_users.id,
        purpose=FilePurpose.EVALUATION_DATASET,
    )


@pytest.mark.django_db()
class TestCSVUploadCreate:
    def test_csv_upload_column_parsing(self, client_with_user, team_with_users, csv_file_content):
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

    def test_csv_dataset_creation_without_history(self, client_with_user, team_with_users, csv_file_instance):
        """Test creating dataset from CSV without populate_history."""
        column_mapping = {
            "input": "input",
            "output": "output",
            "context": {"context_field": "context_field", "date": "date"},
        }

        form_data = {
            "name": "Test CSV Dataset",
            "mode": "csv",
            "csv_file_id": str(csv_file_instance.id),
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

    def test_csv_dataset_creation_with_history(self, client_with_user, team_with_users, csv_file_instance):
        """Test creating dataset from CSV with populate_history enabled."""
        column_mapping = {
            "input": "input",
            "output": "output",
            "context": {"context_field": "context_field", "date": "date"},
        }

        form_data = {
            "name": "Test CSV Dataset with History",
            "mode": "csv",
            "csv_file_id": str(csv_file_instance.id),
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
        assert second_message.history[0]["message_type"] == "human"
        assert second_message.history[0]["content"] == "Hello"
        assert second_message.history[1]["message_type"] == "ai"
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

    def test_csv_column_suggestions_algorithm(self):
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

    def test_csv_column_suggestions_with_prefixed_columns(self):
        """Test that prefixed columns (from downloaded CSVs) are correctly categorized."""
        columns = [
            "input",
            "output",
            "context.topic",
            "context.difficulty",
            "participant_data.age",
            "participant_data.name",
            "session_state.step",
            "session_state.completed",
            "history",
        ]
        suggestions = generate_csv_column_suggestions(columns)

        assert suggestions["input"] == "input"
        assert suggestions["output"] == "output"
        assert suggestions["history"] == "history"

        assert "context" in suggestions
        context_mappings = {m["fieldName"]: m["csvColumn"] for m in suggestions["context"]}
        assert context_mappings["topic"] == "context.topic"
        assert context_mappings["difficulty"] == "context.difficulty"

        assert "participant_data" in suggestions
        pd_mappings = {m["fieldName"]: m["csvColumn"] for m in suggestions["participant_data"]}
        assert pd_mappings["age"] == "participant_data.age"
        assert pd_mappings["name"] == "participant_data.name"

        assert "session_state" in suggestions
        ss_mappings = {m["fieldName"]: m["csvColumn"] for m in suggestions["session_state"]}
        assert ss_mappings["step"] == "session_state.step"
        assert ss_mappings["completed"] == "session_state.completed"

    def test_csv_with_empty_rows_handling(self, client_with_user, team_with_users):
        """Test CSV upload handles empty or invalid rows correctly."""
        # Create CSV content
        csv_content = (
            "input,output,context\n"
            "Hello,Hi!,valid\n"
            ",Response,missing_input\n"
            "Question,,missing_output\n"
            "Valid,Valid response,valid2\n"
        )
        csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")
        file_instance = File.create(
            filename="test.csv",
            file_obj=csv_file,
            team_id=team_with_users.id,
            purpose=FilePurpose.EVALUATION_DATASET,
        )

        form_data = {
            "name": "Dataset with Empty Rows",
            "mode": "csv",
            "csv_file_id": str(file_instance.id),
            "column_mapping": json.dumps({"input": "input", "output": "output", "context": {"context": "context"}}),
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

    def test_csv_dataset_creation_with_generated_history(self, client_with_user, team_with_users):
        """Test creating dataset from CSV using a history column."""
        csv_content = (
            "input,output,topic,history\n"
            '"What\'s the weather?","I can\'t check weather directly.",weather,'
            '"user: Hello\nassistant: Hi there!\nuser: How are you?\nassistant: I\'m doing well, thanks!"\n'
            '"Tell me a joke","Why don\'t scientists trust atoms? Because they make up everything!",humor,'
            '"user: Previous conversation\nassistant: Previous response"\n'
        )
        csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")
        file_instance = File.create(
            filename="test.csv",
            file_obj=csv_file,
            team_id=team_with_users.id,
            purpose=FilePurpose.EVALUATION_DATASET,
        )

        column_mapping = {"input": "input", "output": "output", "context": {"topic": "topic"}}

        form_data = {
            "name": "Test CSV Dataset with History Column",
            "mode": "csv",
            "csv_file_id": str(file_instance.id),
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

    def test_csv_dataset_creation_with_participant_data_and_session_state(self, client_with_user, team_with_users):
        """Test creating dataset from CSV with participant_data and session_state fields."""
        # Create CSV content
        csv_content = "input,output,age,name,step,completed\n"
        csv_content += "What is AI?,AI stands for Artificial Intelligence,25,John,1,false\n"
        csv_content += "Tell me more,AI is used in many applications,30,Jane,2,true\n"

        csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")
        file_instance = File.create(
            filename="test.csv",
            file_obj=csv_file,
            team_id=team_with_users.id,
            purpose=FilePurpose.EVALUATION_DATASET,
        )

        # Map columns to participant_data and session_state using nested structure
        column_mapping = {
            "input": "input",
            "output": "output",
            "participant_data": {"age": "age", "name": "name"},
            "session_state": {"step": "step", "completed": "completed"},
        }

        form_data = {
            "name": "Test Dataset with Participant Data",
            "mode": "csv",
            "csv_file_id": str(file_instance.id),
            "column_mapping": json.dumps(column_mapping),
            "populate_history": False,
        }

        url = reverse("evaluations:dataset_new", args=[team_with_users.slug])
        response = client_with_user.post(url, form_data)

        assert response.status_code == 302

        dataset = EvaluationDataset.objects.get(name="Test Dataset with Participant Data", team=team_with_users)
        assert dataset.messages.count() == 2

        messages = list(dataset.messages.all().order_by("id"))

        # First message
        first_message = messages[0]
        assert first_message.input["content"] == "What is AI?"
        assert first_message.output["content"] == "AI stands for Artificial Intelligence"
        assert first_message.participant_data == {"age": "25", "name": "John"}
        assert first_message.session_state == {"step": "1", "completed": "false"}
        assert first_message.context == {}  # No context fields

        # Second message
        second_message = messages[1]
        assert second_message.input["content"] == "Tell me more"
        assert second_message.output["content"] == "AI is used in many applications"
        assert second_message.participant_data == {"age": "30", "name": "Jane"}
        assert second_message.session_state == {"step": "2", "completed": "true"}


@pytest.mark.django_db()
class TestCSVUploadEdit:
    def test_update_message_preserves_chat_references_when_content_unchanged(self, dataset, team_with_users):
        """Test that chat message references are preserved when content doesn't change."""

        chat = Chat.objects.create(team=team_with_users)
        input_chat_msg = ChatMessage.objects.create(
            chat=chat, content="Test question", message_type=ChatMessageType.HUMAN
        )
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

    def test_update_message_clears_chat_references_when_output_changed(self, dataset, team_with_users):
        """Test that chat message references are cleared when output content changes."""

        chat = Chat.objects.create(team=team_with_users)
        input_chat_msg = ChatMessage.objects.create(
            chat=chat, content="Test question", message_type=ChatMessageType.HUMAN
        )
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

    def test_csv_upload_creates_row(self, dataset):
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

    def test_csv_upload_updates_row(self, dataset):
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

    def test_csv_upload_different_team(self, dataset):
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

    def test_csv_upload_context_changes(self, dataset):
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

    def test_csv_upload_with_participant_data_and_session_state(self, dataset):
        """Test CSV upload with participant_data and session_state fields."""
        mock_progress_recorder = MockProgressRecorder()

        columns = [
            "id",
            "input_content",
            "output_content",
            "context.topic",
            "participant_data.age",
            "participant_data.name",
            "session_state.step",
            "session_state.completed",
            "history",
        ]
        rows = [
            {
                "id": "",
                "input_content": "What is AI?",
                "output_content": "AI stands for Artificial Intelligence",
                "context.topic": "technology",
                "participant_data.age": "25",
                "participant_data.name": "John",
                "session_state.step": "1",
                "session_state.completed": "false",
                "history": "",
            }
        ]

        stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
        assert stats["created_count"] == 1
        assert stats["updated_count"] == 0
        assert len(stats["error_messages"]) == 0

        # Get the newly created message (not the one from the factory)
        message = dataset.messages.order_by("-id").first()
        assert message.input["content"] == "What is AI?"
        assert message.context == {"topic": "technology"}
        assert message.participant_data == {"age": "25", "name": "John"}
        assert message.session_state == {"step": "1", "completed": "false"}

    def test_csv_upload_updates_participant_data_and_session_state(self, dataset):
        """Test CSV upload updates existing messages with new participant_data and session_state."""
        mock_progress_recorder = MockProgressRecorder()

        existing_message = EvaluationMessage.objects.create(
            input=EvaluationMessageContent(content="Test question", role="human").model_dump(),
            output=EvaluationMessageContent(content="Test answer", role="ai").model_dump(),
            context={"topic": "test"},
            participant_data={"age": "20", "old_field": "remove_me"},
            session_state={"step": "0"},
            history=[],
            metadata={"created_mode": "manual"},
        )
        dataset.messages.add(existing_message)

        columns = [
            "id",
            "input_content",
            "output_content",
            "context.topic",
            "participant_data.age",
            "participant_data.name",
            "session_state.step",
            "session_state.completed",
            "history",
        ]
        rows = [
            {
                "id": str(existing_message.id),
                "input_content": "Test question",
                "output_content": "Test answer",
                "context.topic": "test",
                "participant_data.age": "25",  # updated
                "participant_data.name": "John",  # new field
                "session_state.step": "1",  # updated
                "session_state.completed": "true",  # new field
                "history": "",
            }
        ]

        stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
        assert stats["created_count"] == 0
        assert stats["updated_count"] == 1
        assert len(stats["error_messages"]) == 0

        existing_message.refresh_from_db()
        assert existing_message.participant_data == {"age": "25", "name": "John"}
        assert "old_field" not in existing_message.participant_data
        assert existing_message.session_state == {"step": "1", "completed": "true"}

    def test_csv_upload_with_nested_json_values(self, dataset):
        """Test CSV upload handles nested JSON values in participant_data and session_state."""
        import json

        mock_progress_recorder = MockProgressRecorder()

        nested_preferences = {"theme": "dark", "notifications": {"email": True, "sms": False}}
        nested_workflow = {"steps": ["start", "middle", "end"], "current": 1}

        columns = [
            "id",
            "input_content",
            "output_content",
            "participant_data.preferences",
            "session_state.workflow",
            "history",
        ]
        rows = [
            {
                "id": "",
                "input_content": "Complex data",
                "output_content": "Understood",
                "participant_data.preferences": json.dumps(nested_preferences),
                "session_state.workflow": json.dumps(nested_workflow),
                "history": "",
            }
        ]

        stats = process_csv_rows(dataset, rows, columns, mock_progress_recorder, dataset.team)
        assert stats["created_count"] == 1
        assert stats["updated_count"] == 0
        assert len(stats["error_messages"]) == 0

        # Get the newly created message (not the one from the factory)
        message = dataset.messages.order_by("-id").first()
        assert message.input["content"] == "Complex data"
        assert message.participant_data == {"preferences": nested_preferences}
        assert message.session_state == {"workflow": nested_workflow}
