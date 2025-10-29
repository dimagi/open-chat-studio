import pytest
from django.test import override_settings

from apps.chat.models import ChatMessageType
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMessage
from apps.evaluations.tasks import create_dataset_from_sessions_task
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_with_filtered_sessions_no_filter():
    """Test cloning sessions when filtered sessions are selected without filters - should get all messages."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message1 ai", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="message2 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="message2 ai", chat=session_1.chat)

    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset")

    task_result = create_dataset_from_sessions_task.delay(
        dataset.id,
        team.id,
        [],  # No regular sessions
        [session_1.external_id],  # Filtered sessions
        "",  # No filter params
        None,  # No timezone
    )

    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 2
    assert result["duplicates_skipped"] == 0

    dataset.refresh_from_db()

    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert dataset.job_id == ""

    messages = list(dataset.messages.all())
    assert len(messages) == 2

    messages.sort(key=lambda m: m.input_chat_message.id)
    assert messages[0].input["content"] == "message1 human"
    assert messages[0].output["content"] == "message1 ai"
    assert messages[1].input["content"] == "message2 human"
    assert messages[1].output["content"] == "message2 ai"


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_with_filter():
    """Test cloning sessions with filter - should only get filtered messages."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    human_msg1 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="good message", chat=session_1.chat)
    human_msg1.add_rating("+1")  # This will be included
    ChatMessageFactory(message_type=ChatMessageType.AI, content="good response", chat=session_1.chat)

    human_msg2 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="bad message", chat=session_1.chat)
    human_msg2.add_rating("-1")  # This will be filtered out
    ChatMessageFactory(message_type=ChatMessageType.AI, content="bad response", chat=session_1.chat)

    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset")
    filter_params = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])

    task_result = create_dataset_from_sessions_task.delay(
        dataset.id,
        team.id,
        [],  # No regular sessions
        [session_1.external_id],  # Filtered session
        filter_params.to_query(),  # Filter query
        None,  # No timezone
    )
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 1
    assert result["duplicates_skipped"] == 0

    dataset.refresh_from_db()

    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert dataset.job_id == ""

    messages = list(dataset.messages.all())
    assert len(messages) == 1
    assert messages[0].input["content"] == "good message"
    assert messages[0].output["content"] == "good response"


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_with_duplicates():
    """Test that duplicate detection works when adding messages to existing dataset."""
    session_1 = ExperimentSessionFactory()
    team = session_1.team

    human_msg = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="test message", chat=session_1.chat)
    ai_msg = ChatMessageFactory(message_type=ChatMessageType.AI, content="test response", chat=session_1.chat)

    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset")

    existing_msg = EvaluationMessage.objects.create(
        input={"content": "test message", "role": "human"},
        output={"content": "test response", "role": "ai"},
        input_chat_message=human_msg,
        expected_output_chat_message=ai_msg,
    )
    dataset.messages.add(existing_msg)

    task_result = create_dataset_from_sessions_task.delay(
        dataset.id,
        team.id,
        [session_1.external_id],
        [],
        None,
        None,
    )

    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 0
    assert result["duplicates_skipped"] == 1

    dataset.refresh_from_db()

    assert dataset.status == DatasetCreationStatus.COMPLETED

    messages = list(dataset.messages.all())
    assert len(messages) == 1
