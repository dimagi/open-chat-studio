from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.tasks import async_create_experiment_version, async_export_chat, get_response_for_webchat_task
from apps.files.models import File, FilePurpose
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_async_export_chat_returns_file_id():
    session = ExperimentSessionFactory.create()
    result = async_export_chat.run(session.experiment_id, {}, "UTC")
    file = File.objects.get(id=result["file_id"])
    assert file.purpose == FilePurpose.DATA_EXPORT
    assert file.expiry_date is not None


@pytest.mark.django_db()
@patch("apps.experiments.tasks.ProgressRecorder")
def test_async_export_chat_reports_progress(mock_recorder_cls):
    recorder = mock_recorder_cls.return_value
    session = ExperimentSessionFactory.create()
    for i in range(2):
        ChatMessage.objects.create(chat=session.chat, content=f"m{i}", message_type=ChatMessageType.HUMAN)

    async_export_chat.run(session.experiment_id, {}, "UTC")

    # Final progress update reports all messages processed against the total.
    recorder.set_progress.assert_called_with(2, 2, description="Processing 2 of 2 messages")


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_async_create_experiment_version():
    experiment = ExperimentFactory.create(create_version_task_id="asd123")
    async_create_experiment_version(experiment.id)
    assert experiment.versions.count() == 1
    experiment.refresh_from_db()
    assert experiment.create_version_task_id == ""


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch("apps.experiments.models.Experiment.create_new_version")
def test_async_create_experiment_version_fails(create_new_version):
    create_new_version.side_effect = Exception("Error")
    experiment = ExperimentFactory.create(create_version_task_id="asd123")
    with pytest.raises(Exception, match="Error"):
        async_create_experiment_version(experiment.id)
    assert experiment.versions.count() == 0
    experiment.refresh_from_db()
    assert experiment.create_version_task_id == ""


@pytest.mark.django_db()
@patch("apps.experiments.tasks.WebChannel")
def test_get_response_for_webchat_task_merges_context(mock_web_channel):
    """Test that context is stored in session state at remote_context key"""
    session = ExperimentSessionFactory.create(state={})
    context_data = {"page_url": "https://example.com", "user_info": "test_user"}

    mock_channel_instance = MagicMock()
    mock_web_channel.return_value = mock_channel_instance
    mock_chat_message = MagicMock()
    mock_chat_message.content = "Hello bot"
    mock_chat_message.id = 1
    mock_channel_instance.new_user_message.return_value = mock_chat_message

    get_response_for_webchat_task(
        session.id,
        session.experiment_id,
        "Hello bot",
        context=context_data,
    )

    session.refresh_from_db()
    assert session.state["remote_context"] == context_data
