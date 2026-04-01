from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessageType
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMessage, EvaluationMode
from apps.evaluations.tasks import create_session_mode_dataset_task
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_session_mode_dataset_task_success():
    """Task creates one EvaluationMessage per session and sets status to COMPLETED."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    task_result = create_session_mode_dataset_task.delay(dataset.id, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 1
    assert result["duplicates_skipped"] == 0

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert dataset.job_id == ""

    messages = list(dataset.messages.all())
    assert len(messages) == 1
    assert messages[0].input == {}
    assert messages[0].output == {}
    assert len(messages[0].history) == 2


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_session_mode_dataset_task_all_duplicates():
    """Task returns success with 0 created_count when all sessions are already in the dataset."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    existing_message = EvaluationMessage.objects.create(
        input={},
        output={},
        history=[],
        metadata={"session_id": str(session.external_id), "created_mode": "clone"},
    )
    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset 2", evaluation_mode=EvaluationMode.SESSION
    )
    dataset.messages.add(existing_message)

    task_result = create_session_mode_dataset_task.delay(dataset.id, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 0
    assert result["duplicates_skipped"] == 1

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    # Original message still there, no duplicate added
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_session_mode_dataset_task_dataset_not_found():
    """Task returns error dict when dataset doesn't exist."""
    session = ExperimentSessionFactory.create()
    team = session.team

    task_result = create_session_mode_dataset_task.delay(99999, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is False
    assert "error" in result


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_session_mode_dataset_task_error_path():
    """Task handles exceptions by saving FAILED status to dataset."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset 3", evaluation_mode=EvaluationMode.SESSION
    )

    with patch("apps.evaluations.utils.make_session_evaluation_messages", side_effect=Exception("DB error")):
        task_result = create_session_mode_dataset_task.delay(dataset.id, team.id, [session.external_id])
        result = task_result.get()

    assert result["success"] is False

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.FAILED
