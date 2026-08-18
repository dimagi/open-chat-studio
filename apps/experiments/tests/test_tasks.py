import gzip
import logging
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments import tasks
from apps.experiments.tasks import async_create_experiment_version, async_export_chat, get_response_for_webchat_task
from apps.files.models import File, FilePurpose
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_async_export_chat_returns_file_id():
    session = ExperimentSessionFactory.create()
    result = async_export_chat.run(session.experiment_id, "", "UTC")
    file = File.objects.get(id=result["file_id"])
    assert file.purpose == FilePurpose.DATA_EXPORT
    assert file.expiry_date is not None


@pytest.mark.django_db()
@patch("apps.experiments.tasks.ProgressRecorder")
def test_async_export_chat_applies_query_string_filters(mock_recorder_cls):
    """The task takes the raw query string, not a QueryDict.

    Celery's JSON serializer flattens a QueryDict into a plain dict, which has no
    `.getlist()` — passing one through made the task raise
    ``AttributeError: 'dict' object has no attribute 'getlist'``. Two filters on the same
    column exercise the multi-value handling that only a QueryDict provides.
    """
    experiment = ExperimentFactory.create()
    included = ExperimentSessionFactory.create(experiment=experiment, participant__identifier="alice")
    excluded = ExperimentSessionFactory.create(experiment=experiment, participant__identifier="bob")
    for session in (included, excluded):
        ChatMessage.objects.create(
            chat=session.chat,
            content=f"message from {session.participant.identifier}",
            message_type=ChatMessageType.HUMAN,
        )

    query_string = "f_participant=alice&op_participant=equals&f_participant=bob&op_participant=does+not+contain"
    result = async_export_chat.run(experiment.id, query_string, "UTC")

    csv_content = gzip.decompress(File.objects.get(id=result["file_id"]).file.read()).decode()
    assert "message from alice" in csv_content
    assert "message from bob" not in csv_content


class _ReadTracker:
    """Wraps the export temp file and records the size argument of every ``read()`` call."""

    def __init__(self, wrapped, reads):
        self._wrapped = wrapped
        self._reads = reads

    def read(self, size=-1):
        self._reads.append(size)
        return self._wrapped.read(size)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._wrapped.__exit__(*exc_info)


@pytest.mark.django_db()
@patch("apps.experiments.tasks.ProgressRecorder")
def test_async_export_chat_streams_temp_file_to_storage(mock_recorder_cls):
    """The export must reach storage in bounded chunks, never as one big ``read()``.

    ``ContentFile(tmp.read(), ...)`` materialised the entire compressed export as a single
    bytes object, so peak worker memory scaled with export size and large exports OOM'd the
    worker — defeating the point of spooling to a temp file in the first place.
    """
    session = ExperimentSessionFactory.create()
    for i in range(50):
        ChatMessage.objects.create(chat=session.chat, content=f"m{i}", message_type=ChatMessageType.HUMAN)

    reads = []
    real_export_to_tempfile = tasks.export_to_tempfile

    def tracked_export_to_tempfile(*args, **kwargs):
        return _ReadTracker(real_export_to_tempfile(*args, **kwargs), reads)

    with patch.object(tasks, "export_to_tempfile", tracked_export_to_tempfile):
        result = async_export_chat.run(session.experiment_id, "", "UTC")

    assert reads, "the temp file was never read"
    assert all(size > 0 for size in reads), f"export was read unbounded into memory: read sizes {reads}"

    file = File.objects.get(id=result["file_id"])
    csv_content = gzip.decompress(file.file.read()).decode()
    assert "m49" in csv_content
    assert file.content_size == file.file.size


@pytest.mark.django_db()
@patch("apps.experiments.tasks.ProgressRecorder")
def test_async_export_chat_reports_progress(mock_recorder_cls, caplog):
    recorder = mock_recorder_cls.return_value
    session = ExperimentSessionFactory.create()
    for i in range(2):
        ChatMessage.objects.create(chat=session.chat, content=f"m{i}", message_type=ChatMessageType.HUMAN)

    with caplog.at_level(logging.INFO, logger="ocs.experiments"):
        async_export_chat.run(session.experiment_id, "", "UTC")

    # Final progress update reports all messages processed against the total.
    recorder.set_progress.assert_called_with(2, 2, description="Processing 2 of 2 messages")
    # Progress is also logged (with the experiment name) so it can be tracked in the shell.
    assert f"Chat export '{session.experiment.name}': processed 2/2 messages" in caplog.text


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
