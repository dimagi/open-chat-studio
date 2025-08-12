import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent
from apps.evaluations.tasks import _update_existing_message
from apps.evaluations.utils import parse_history_text
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_upload_dataset_csv_task_start(client, team_with_users):
    """Test that CSV upload task starts correctly."""
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    csv_content = "id,input_content,output_content,context.topic,history\n"
    csv_content += "999,What is AI?,AI is Artificial Intelligence,technology,\n"  # Non-existent ID
    csv_content += (
        ",Tell me about Python,Python is a programming language,programming,user: Hello\\nassistant: Hi there!\n"
    )

    csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")

    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_upload", args=[team_with_users.slug, dataset.id])
    response = client.post(url, {"csv_file": csv_file})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "task_id" in data


@pytest.mark.django_db()
def test_upload_dataset_csv_with_existing_messages(client, team_with_users):
    """Test CSV upload with existing messages to update."""
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    existing_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Original question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Original answer", role="ai").model_dump(),
        context={"topic": "original"},
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(existing_message)

    csv_content = "id,input_content,output_content,context.topic,history\n"
    csv_content += (
        f"{existing_message.id},Updated question,Updated answer,updated,user: Previous\\nassistant: Response\n"
    )

    csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")

    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_upload", args=[team_with_users.slug, dataset.id])
    response = client.post(url, {"csv_file": csv_file})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "task_id" in data


@pytest.mark.django_db()
def test_upload_csv_security_check(client, team_with_users):
    """Test that CSV upload properly validates team access."""

    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    # Create second team
    other_team = TeamWithUsersFactory.create()
    other_user = other_team.members.first()

    csv_content = "id,input_content,output_content\n1,Test,Test\n"
    csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")

    # Login as user from different team
    client.force_login(other_user)

    # Try to upload CSV to first team's dataset
    url = reverse("evaluations:dataset_upload", args=[team_with_users.slug, dataset.id])
    response = client.post(url, {"csv_file": csv_file})

    # Should be denied access
    assert response.status_code == 404  # get_object_or_404 returns 404 for security


@pytest.mark.django_db()
def test_upload_csv_validation(client, team_with_users):
    """Test CSV upload validation."""
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)
    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_upload", args=[team_with_users.slug, dataset.id])

    # Test empty file
    response = client.post(url, {})
    assert response.status_code == 400
    assert "No CSV file provided" in response.json()["error"]

    # Test empty CSV
    empty_csv = SimpleUploadedFile("empty.csv", b"", content_type="text/csv")
    response = client.post(url, {"csv_file": empty_csv})
    assert response.status_code == 400
    assert "empty" in response.json()["error"]


@pytest.mark.django_db()
def test_parse_history_functionality():
    """Test the history parsing functionality."""

    # Test empty history
    assert parse_history_text("") == []

    # Test single line history
    history_text = "human: Hello there"
    result = parse_history_text(history_text)
    assert len(result) == 1
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello there"

    # Test multi-line history
    history_text = "human: Hello\nai: Hi there!\nhuman: How are you?"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"
    assert result[1]["message_type"] == "ai"
    assert result[2]["message_type"] == "human"

    # Test message with newlines in content
    history_text = "human: This is a multi-line\nmessage with newlines\nai: I understand your\nmulti-line message"
    result = parse_history_text(history_text)
    assert len(result) == 2
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "This is a multi-line\nmessage with newlines"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "I understand your\nmulti-line message"

    # Test garbled messages (lines that don't start with role markers)
    history_text = (
        "This is garbled text\nhuman: Hello\nsome random text without role\nai: Hi there!\nmore garbled content"
    )
    result = parse_history_text(history_text)
    assert len(result) == 2  # Only the valid human/ai messages should be parsed
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello\nsome random text without role"  # Continuation line included
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Hi there!\nmore garbled content"  # Continuation line included

    # Test different casings (HUMAN, Human, AI, Ai, etc.)
    history_text = "HUMAN: Hello from uppercase\nAi: Mixed case response\nhuman: lowercase again"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"  # Always normalized to lowercase
    assert result[0]["content"] == "Hello from uppercase"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Mixed case response"
    assert result[2]["message_type"] == "human"
    assert result[2]["content"] == "lowercase again"


@pytest.mark.django_db()
def test_update_message_preserves_chat_references_when_content_unchanged():
    """Test that chat message references are preserved when content doesn't change."""

    team = TeamWithUsersFactory.create()
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team)

    # Create chat messages to reference
    chat = Chat.objects.create(team=team)
    input_chat_msg = ChatMessage.objects.create(chat=chat, content="Test question", message_type=ChatMessageType.HUMAN)
    output_chat_msg = ChatMessage.objects.create(chat=chat, content="Test answer", message_type=ChatMessageType.AI)

    # Create message with chat references
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

    _update_existing_message(dataset, message.id, row_data_unchanged, team)
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

    _update_existing_message(dataset, message.id, row_data_changed, team)
    message.refresh_from_db()

    assert message.input_chat_message is None
    assert message.expected_output_chat_message is not None  # Shouldn't be removed
    assert message.input["content"] == "Changed question"
    assert message.context == {"topic": "changed"}


@pytest.mark.django_db()
def test_update_message_clears_chat_references_when_output_changed():
    """Test that chat message references are cleared when output content changes."""

    team = TeamWithUsersFactory.create()
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team)

    chat = Chat.objects.create(team=team)
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

    _update_existing_message(dataset, message.id, row_data_changed, team)
    message.refresh_from_db()

    assert message.input_chat_message is not None  # Same as before
    assert message.expected_output_chat_message is None
    assert message.output["content"] == "Changed answer"


@pytest.mark.django_db()
def test_csv_upload_comprehensive_scenarios(client, team_with_users):
    """Test comprehensive CSV upload scenarios:
    1. Create new row with history and context
    2. Update existing message with context changes (additions, deletions, modifications)
    3. Try to update message from different team (should fail)
    """
    dataset = EvaluationDataset.objects.create(name="Test Dataset", team=team_with_users)

    # Create existing message with initial context
    existing_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Original question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Original answer", role="ai").model_dump(),
        context={"topic": "tech", "difficulty": "easy", "old_field": "to_be_removed"},
        history=[],
        metadata={"created_mode": "manual"},
    )
    dataset.messages.add(existing_message)

    # Create a second team and message that shouldn't be accessible
    other_team = TeamWithUsersFactory.create()
    other_dataset = EvaluationDataset.objects.create(name="Other Dataset", team=other_team)
    other_message = EvaluationMessage.objects.create(
        input=EvaluationMessageContent(content="Other question", role="human").model_dump(),
        output=EvaluationMessageContent(content="Other answer", role="ai").model_dump(),
        context={"topic": "other"},
        metadata={"created_mode": "manual"},
    )
    other_dataset.messages.add(other_message)

    # CSV content with:
    # 1. New row with history and context
    # 2. Update existing message with context changes
    # 3. Attempt to update message from different team
    csv_content = "id,input_content,output_content,context.topic,context.difficulty,context.new_field,history\n"

    # New message with history and context
    csv_content += (
        ",New question with history,New answer,science,hard,added_value,"
        '"human: Hello\nai: Hi there!\nhuman: How are you?"\n'
    )

    # Update existing message with context changes
    csv_content += (
        f"{existing_message.id},Updated question,Updated answer,science,medium,new_added_field,"
        '"human: Previous chat\nai: Previous response"\n'
    )

    # Try to update message from different team (should fail)
    csv_content += f'{other_message.id},Hack attempt,Hacked,hacking,expert,malicious,"human: Bad\nai: Very bad"\n'

    csv_file = SimpleUploadedFile("test.csv", csv_content.encode(), content_type="text/csv")

    user = team_with_users.members.first()
    client.force_login(user)

    url = reverse("evaluations:dataset_upload", args=[team_with_users.slug, dataset.id])
    response = client.post(url, {"csv_file": csv_file})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "task_id" in data

    # Since we can't wait for the actual task to complete in the test,
    # let's directly test the helper functions used by the task
    from apps.evaluations.tasks import _create_new_message, _extract_row_data, _update_existing_message

    # Test scenario 1: New message with history and context
    new_row = {
        "id": "",
        "input_content": "New question with history",
        "output_content": "New answer",
        "context.topic": "science",
        "context.difficulty": "hard",
        "context.new_field": "added_value",
        "history": "human: Hello\nai: Hi there!\nhuman: How are you?",
    }

    row_data = _extract_row_data(new_row, 0)
    _create_new_message(dataset, row_data)

    # Verify new message was created correctly
    new_message = EvaluationMessage.objects.filter(input__content="New question with history").first()
    assert new_message is not None
    assert new_message.input["content"] == "New question with history"
    assert new_message.output["content"] == "New answer"
    assert new_message.context == {"topic": "science", "difficulty": "hard", "new_field": "added_value"}
    assert len(new_message.history) == 3
    assert new_message.history[0]["message_type"] == "human"
    assert new_message.history[0]["content"] == "Hello"
    assert new_message.history[1]["message_type"] == "ai"
    assert new_message.history[1]["content"] == "Hi there!"
    assert new_message.history[2]["message_type"] == "human"
    assert new_message.history[2]["content"] == "How are you?"

    # Test scenario 2: Update existing message with context changes
    update_row = {
        "id": str(existing_message.id),
        "input_content": "Updated question",
        "output_content": "Updated answer",
        "context.topic": "science",  # changed from "tech"
        "context.difficulty": "medium",  # changed from "easy"
        "context.new_field": "new_added_field",  # new field added
        # Note: "old_field" is missing, so it should be removed
        "history": "human: Previous chat\nai: Previous response",
    }

    update_row_data = _extract_row_data(update_row, 1)
    _update_existing_message(dataset, existing_message.id, update_row_data, team_with_users)

    # Verify existing message was updated correctly
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

    # Test scenario 3: Try to update message from different team (should fail)
    cross_team_row = {
        "id": str(other_message.id),
        "input_content": "Hack attempt",
        "output_content": "Hacked",
        "context.topic": "hacking",
        "history": "human: Bad\nai: Very bad",
    }

    cross_team_row_data = _extract_row_data(cross_team_row, 2)

    # This should raise EvaluationMessage.DoesNotExist because of team filtering
    with pytest.raises(EvaluationMessage.DoesNotExist):
        _update_existing_message(dataset, other_message.id, cross_team_row_data, team_with_users)

    # Verify the other team's message was not modified
    other_message.refresh_from_db()
    assert other_message.input["content"] == "Other question"  # unchanged
    assert other_message.output["content"] == "Other answer"  # unchanged
    assert other_message.context == {"topic": "other"}  # unchanged

    # Test scenario 4: Update with only context changes (no content or history changes)
    # This should preserve any existing chat references
    context_only_row = {
        "id": str(existing_message.id),
        "input_content": "Updated question",  # Same as current (after previous update)
        "output_content": "Updated answer",  # Same as current
        "context.topic": "physics",  # Changed context
        "context.difficulty": "hard",  # Changed context
        "context.new_field": "context_only_change",  # Changed context
        "history": "human: Previous chat\nai: Previous response",  # Same history as current
    }

    context_only_row_data = _extract_row_data(context_only_row, 3)
    result_changed = _update_existing_message(dataset, existing_message.id, context_only_row_data, team_with_users)

    # Verify that no content changes were detected (only context changed)
    assert result_changed is False  # Should return False since no content/history changed

    existing_message.refresh_from_db()
    assert existing_message.context == {
        "topic": "physics",  # changed
        "difficulty": "hard",  # changed
        "new_field": "context_only_change",  # changed
    }

    # Test scenario 5: Update with changed history only
    history_only_row = {
        "id": str(existing_message.id),
        "input_content": "Updated question",  # Same as current
        "output_content": "Updated answer",  # Same as current
        "context.topic": "physics",  # Same as current
        "context.difficulty": "hard",  # Same as current
        "context.new_field": "context_only_change",  # Same as current
        "history": "human: New history line\nai: Different response",  # Changed history
    }

    history_only_row_data = _extract_row_data(history_only_row, 4)
    result_history_changed = _update_existing_message(
        dataset, existing_message.id, history_only_row_data, team_with_users
    )

    # Verify that content changes were detected due to history change
    assert result_history_changed is True  # Should return True since history changed

    existing_message.refresh_from_db()
    assert len(existing_message.history) == 2
    assert existing_message.history[0]["message_type"] == "human"
    assert existing_message.history[0]["content"] == "New history line"
    assert existing_message.history[1]["message_type"] == "ai"
    assert existing_message.history[1]["content"] == "Different response"
    # Metadata should be updated due to history change
    assert existing_message.metadata.get("last_modified") == "csv_upload"
