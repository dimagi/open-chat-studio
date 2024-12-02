from contextlib import nullcontext as does_not_raise
from typing import Literal
from unittest import mock
from unittest.mock import Mock, patch

import openai
import pytest
from openai.types.beta.threads import ImageFile, ImageFileContentBlock, Run
from openai.types.beta.threads import Message as ThreadMessage
from openai.types.beta.threads.file_citation_annotation import FileCitation, FileCitationAnnotation
from openai.types.beta.threads.file_path_annotation import FilePath, FilePathAnnotation
from openai.types.beta.threads.text import Text
from openai.types.beta.threads.text_content_block import TextContentBlock
from openai.types.file_object import FileObject

from apps.channels.datamodels import Attachment
from apps.chat.agent.tools import TOOL_CLASS_MAP
from apps.chat.models import Chat, ChatAttachment, ChatMessage
from apps.service_providers.llm_service.adapters import AssistantAdapter
from apps.service_providers.llm_service.runnables import (
    AssistantChat,
    GenerationCancelled,
    GenerationError,
    create_experiment_runnable,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.langchain import mock_experiment_llm

ASSISTANT_ID = "test_assistant_id"


@pytest.fixture(params=[True, False], ids=["with_tools", "without_tools"])
def session(request):
    chat = Chat()
    chat.save = lambda: None
    session = ExperimentSessionFactory.build(chat=chat)
    local_assistant = OpenAiAssistantFactory.build(id=1, assistant_id=ASSISTANT_ID, include_file_info=False)
    if request.param:
        local_assistant.tools = list(TOOL_CLASS_MAP.keys())

    local_assistant.has_custom_actions = lambda *args, **kwargs: False

    session.experiment.assistant = local_assistant
    session.get_participant_data = lambda *args, **kwargs: None
    session.get_participant_timezone = lambda *args, **kwargs: ""
    return session


@pytest.fixture(params=[True, False], ids=["with_tools", "without_tools"])
def db_session(request):
    local_assistant = OpenAiAssistantFactory(
        id=1, assistant_id=ASSISTANT_ID, tools=list(TOOL_CLASS_MAP.keys()) if request.param else []
    )
    session = ExperimentSessionFactory(id=1)
    session.experiment.assistant = local_assistant
    session.experiment.save()
    return session


@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
def test_assistant_conversation_new_chat(
    create_and_run, retrieve_run, list_messages, save_response_annotations, session
):
    save_response_annotations.return_value = ("ai response", {})
    chat = session.chat
    assert chat.thread_id is None

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    create_and_run.return_value = run
    retrieve_run.return_value = run

    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": "ai response"}]
    )

    assistant_runnable = create_experiment_runnable(session.experiment, session)

    result = assistant_runnable.invoke("test")
    assert result.output == "ai response"
    assert chat.thread_id == thread_id


@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.messages.Messages.create")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.threads.runs.Runs.create")
def test_assistant_conversation_existing_chat(
    create_run, retrieve_run, create_message, list_messages, save_response_annotations, session
):
    ai_response = "ai response"
    save_response_annotations.return_value = (ai_response, {})
    thread_id = "test_thread_id"
    chat = session.chat
    chat.set_metadata(chat.MetadataKeys.OPENAI_THREAD_ID, thread_id)

    run = _create_run(ASSISTANT_ID, thread_id)
    create_run.return_value = run
    retrieve_run.return_value = run
    list_messages.return_value = _create_thread_messages(ASSISTANT_ID, run.id, thread_id, [{"assistant": ai_response}])

    assistant_runnable = create_experiment_runnable(session.experiment, session)
    result = assistant_runnable.invoke("test")

    assert create_message.call_args.args == (thread_id,)
    assert create_run.call_args.args == (thread_id,)
    assert result.output == "ai response"


@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
def test_assistant_conversation_input_formatting(
    create_and_run, retrieve_run, list_messages, save_response_annotations, session
):
    ai_response = "ai response"
    save_response_annotations.return_value = (ai_response, {})

    session.experiment.input_formatter = "foo {input} bar"

    chat = session.chat
    assert chat.thread_id is None

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    create_and_run.return_value = run
    retrieve_run.return_value = run
    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": "ai response"}]
    )

    assistant_runnable = create_experiment_runnable(session.experiment, session)
    result = assistant_runnable.invoke("test")
    assert result.output == "ai response"
    assert create_and_run.call_args.kwargs["thread"]["messages"][0]["content"] == "foo test bar"


@pytest.mark.django_db()
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_file_type_info")
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
def test_assistant_includes_file_type_information(
    create_and_run, retrieve_run, list_messages, save_response_annotations, get_file_type_info, session
):
    ai_response = "ai response"
    save_response_annotations.return_value = (ai_response, {})

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    create_and_run.return_value = run
    retrieve_run.return_value = run
    get_file_type_info.return_value = [{"file-12345": "application/fmt"}]
    list_messages.return_value = _create_thread_messages(ASSISTANT_ID, run.id, thread_id, [{"assistant": ai_response}])
    assistant = session.experiment.assistant
    assistant.instructions = "Help the user"
    assistant.include_file_info = True

    assistant = _get_assistant_mocked_history_recording(
        session, get_attachments_return_value=[ChatAttachment(chat=session.chat, tool_type="code_interpreter")]
    )
    result = assistant.invoke("test")
    assert result.output == ai_response
    expected_instructions = (
        "Help the user\n\nFile type information:\n\n| File Path | Mime Type |\n"
        "| /mnt/data/file-12345 | application/fmt |\n"
    )
    assert create_and_run.call_args.kwargs["instructions"] == expected_instructions


@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
def test_assistant_runnable_raises_error(session):
    experiment = session.experiment

    error = openai.BadRequestError("test", response=mock.Mock(), body={})
    with mock_experiment_llm(experiment, [error]):
        assistant_runnable = create_experiment_runnable(session.experiment, session)
        with pytest.raises(openai.BadRequestError):
            assistant_runnable.invoke("test")


@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
def test_assistant_runnable_handles_cancellation_status(session):
    experiment = session.experiment

    error = ValueError("unexpected status: cancelled")
    with mock_experiment_llm(experiment, [error]):
        assistant_runnable = create_experiment_runnable(session.experiment, session)
        with pytest.raises(GenerationCancelled):
            assistant_runnable.invoke("test")


@pytest.mark.parametrize(
    ("responses", "exception", "output"),
    [
        (
            [
                openai.BadRequestError(
                    "", response=mock.Mock(), body={"message": "thread_abc while a run run_def is active"}
                ),
                "normal response",
            ],
            does_not_raise(),
            "normal response",
        ),
        (
            [
                # response list is cycled to the exception is raised on every call
                openai.BadRequestError(
                    "", response=mock.Mock(), body={"message": "thread_abc while a run run_def is active"}
                )
            ],
            pytest.raises(GenerationError, match="retries"),
            None,
        ),
        (
            [
                openai.BadRequestError(
                    "", response=mock.Mock(), body={"message": "thread_def while a run run_def is active"}
                )
            ],
            pytest.raises(GenerationError, match="Thread ID mismatch"),
            None,
        ),
    ],
)
@patch("apps.chat.agent.tools.get_custom_action_tools", Mock(return_value=[]))
@patch(
    "apps.service_providers.llm_service.adapters.AssistantAdapter.get_messages_to_sync_to_thread",
    Mock(return_value=[]),
)
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.save_message_to_history", Mock())
@patch("apps.service_providers.llm_service.adapters.AssistantAdapter.get_attachments", Mock())
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
def test_assistant_runnable_cancels_existing_run(save_response_annotations, responses, exception, output, session):
    save_response_annotations.return_value = ("normal response", {})
    thread_id = "thread_abc"
    session.chat.set_metadata(session.chat.MetadataKeys.OPENAI_THREAD_ID, thread_id)

    assistant_runnable = create_experiment_runnable(session.experiment, session)
    cancel_run = mock.Mock()
    assistant_runnable.__dict__["_cancel_run"] = cancel_run
    with mock_experiment_llm(session.experiment, responses):
        with exception:
            result = assistant_runnable.invoke("test")

    if output:
        assert result.output == "normal response"
        cancel_run.assert_called_once()


@pytest.mark.django_db()
@patch("apps.assistants.sync.create_files_remote")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
def test_assistant_uploads_new_file(create_and_run, retrieve_run, list_messages, create_files_remote, db_session):
    """Test that attachments are uploaded to OpenAI and that its remote file ids are stored on the chat message"""
    session = db_session
    create_files_remote.return_value = ["openai-file-1", "openai-file-2"]
    files = FileFactory.create_batch(2)
    chat = session.chat
    assert chat.thread_id is None

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    create_and_run.return_value = run
    retrieve_run.return_value = run
    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": "ai response"}]
    )

    assistant = create_experiment_runnable(session.experiment, session)
    attachments = [
        Attachment(type="code_interpreter", file_id=files[0].id),
        Attachment(type="file_search", file_id=files[1].id),
    ]

    result = assistant.invoke("test", attachments=attachments)
    assert result.output == "ai response"
    assert chat.thread_id == thread_id
    message = chat.messages.filter(message_type="human").first()
    assert "openai-file-1" in message.metadata["openai_file_ids"]
    assert "openai-file-2" in message.metadata["openai_file_ids"]


@pytest.mark.django_db()
@pytest.mark.parametrize("cited_file_missing", [False, True])
@patch("openai.resources.files.Files.retrieve")
@patch("apps.assistants.sync.get_and_store_openai_file")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
@patch("openai.resources.beta.threads.messages.Messages.list")
def test_assistant_response_with_annotations(
    list_messages,
    create_and_run,
    retrieve_run,
    get_and_store_openai_file,
    retrieve_openai_file,
    cited_file_missing,
    db_session,
):
    """Test that attachments on AI messages are being saved (only those of type `file_path`)
    OpenAI doesn't allow you to fetch the content of the file that you uploaded, but this isn't an issue, since we
    already have that file as an attachment on the chat object
    """

    # I'm specifying the ids manually to make it easier to follow the expected output string that contains DB ids
    session = db_session
    session.team.slug = "dimagi-test"
    session.team.save()
    chat = session.chat
    openai_generated_file_id = "openai-file-1"
    openai_generated_file = FileFactory(external_id=openai_generated_file_id, id=10, name="test.png")
    get_and_store_openai_file.return_value = openai_generated_file

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)

    local_file_openai_id = "openai-file-2"
    if cited_file_missing:
        # Mock the call to OpenAI to retrieve the file, since it will be called when a file is missing
        retrieve_openai_file.return_value = FileObject(
            id="local_file_openai_id",
            bytes=1,
            created_at=1,
            filename="existing.txt",
            object="file",
            purpose="assistants",
            status="processed",
            status_details=None,
        )
    else:
        local_file = FileFactory(external_id=local_file_openai_id, team=session.team, name="existing.txt", id=9)
        # Attach the local file to the chat
        attachment, _created = chat.attachments.get_or_create(tool_type="file_path")
        attachment.files.add(local_file)

    # Build OpenAI responses
    annotations = [
        FilePathAnnotation(
            end_index=174,
            file_path=FilePath(file_id=openai_generated_file_id),
            start_index=134,
            text="sandbox:/mnt/data/file.txt",
            type="file_path",
        ),
        FileCitationAnnotation(
            end_index=174,
            file_citation=FileCitation(file_id=local_file_openai_id, quote=""),
            start_index=134,
            text="【6:0†source】",
            type="file_citation",
        ),
    ]
    ai_message = (
        "Hi there human. The generated file can be [downloaded here](sandbox:/mnt/data/file.txt)."
        " A made up link to [file1.pdf](https://example.com/download/file-1) "
        "[file2.pdf](https://example.com/download/file-2)"
        " Also, leaves are tree stuff【6:0†source】."
        " Another link to nothing [file3.pdf](https://example.com/download/file-3)"
    )

    assistant = create_experiment_runnable(session.experiment, session)
    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": ai_message}], annotations
    )

    create_and_run.return_value = run
    retrieve_run.return_value = run

    # Run assistant
    result = assistant.invoke("test", attachments=[])

    if cited_file_missing:
        # The cited file link is empty, since it's missing from the DB
        expected_output_message = (
            "![test.png](file:dimagi-test:1:10)\n"
            "Hi there human. The generated file can be [downloaded here](file:dimagi-test:1:10)."
            " A made up link to *file1.pdf* *file2.pdf*"
            " Also, leaves are tree stuff [1]. Another link to nothing *file3.pdf*\n\\[1\\]: existing.txt"
        )
    else:
        expected_output_message = (
            "![test.png](file:dimagi-test:1:10)\n"
            "Hi there human. The generated file can be [downloaded here](file:dimagi-test:1:10)."
            " A made up link to *file1.pdf* *file2.pdf*"
            " Also, leaves are tree stuff [1]. Another link to nothing *file3.pdf*"
            "\n[1]: file:dimagi-test:1:9"
        )
    assert result.output == expected_output_message

    assert chat.thread_id == thread_id
    assert chat.attachments.filter(tool_type="file_path").exists()
    message = chat.messages.filter(message_type="ai").first()
    assert "openai-file-1" in message.metadata["openai_file_ids"]
    assert "openai-file-2" in message.metadata["openai_file_ids"]


@pytest.mark.django_db()
@patch("openai.resources.files.Files.retrieve")
@patch("apps.assistants.sync.get_and_store_openai_file")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
@patch("openai.resources.beta.threads.messages.Messages.list")
def test_assistant_response_with_image_file_content_block(
    list_messages,
    create_and_run,
    retrieve_run,
    get_and_store_openai_file,
    retrieve_openai_file,
    db_session,
):
    """
    Test that ImageFileContentBlock entries in the content array in an OpenAI message response saves the file to a new
    "image_file" tool type
    """
    retrieve_openai_file.return_value = FileObject(
        id="local_file_openai_id",
        bytes=1,
        created_at=1,
        filename="3fac0517-6367-4f92-a1f3-c9d9087c9085",
        object="file",
        purpose="assistants",
        status="processed",
        status_details=None,
    )
    openai_generated_file = FileFactory(external_id="openai-file-1", id=10, team=db_session.team)
    get_and_store_openai_file.return_value = openai_generated_file

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    list_messages.return_value = _create_thread_messages(ASSISTANT_ID, run.id, thread_id, [{"assistant": "Ola"}])
    create_and_run.return_value = run
    retrieve_run.return_value = run
    assistant = create_experiment_runnable(db_session.experiment, db_session)

    # Run assistant
    result = assistant.invoke("test", attachments=[])
    assert result.output == f"![{openai_generated_file.name}](file:{db_session.team.slug}:1:10)\nOla"
    assert db_session.chat.attachments.filter(tool_type="image_file").exists() is True
    assert db_session.chat.attachments.get(tool_type="image_file").files.count() == 1


@pytest.mark.parametrize(
    ("messages", "thread_id", "thread_created", "messages_created"),
    [
        ([], None, False, False),
        ([], "test_thread_id", False, False),
        ([{"role": "user", "content": "hello"}], None, True, False),
        ([{"role": "user", "content": "hello"}], "test_thread_id", False, True),
    ],
)
def test_sync_messages_to_thread(messages, thread_id, thread_created, messages_created):
    adapter = Mock(spec=AssistantAdapter)
    adapter.get_messages_to_sync_to_thread.return_value = messages
    assistant_runnable = AssistantChat(adapter=adapter)
    assistant_runnable._sync_messages_to_thread(thread_id)

    assert adapter.get_messages_to_sync_to_thread.called
    if messages_created:
        assert adapter.assistant_client.beta.threads.messages.create.call_count == len(messages)
    assert adapter.assistant_client.beta.threads.create.called == thread_created
    if thread_created:
        adapter.update_thread_id.assert_called


@pytest.mark.django_db()
def test_get_messages_to_sync_to_thread():
    session = ExperimentSessionFactory(experiment__assistant=OpenAiAssistantFactory())
    chat = session.chat
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(chat=chat, message_type="human", content="hello0", metadata={}),
            ChatMessage(chat=chat, message_type="ai", content="hello1", metadata={"openai_thread_checkpoint": True}),
            ChatMessage(chat=chat, message_type="human", content="hello2", metadata={}),
            ChatMessage(chat=chat, message_type="ai", content="hello3", metadata={}),
        ]
    )
    adapter = AssistantAdapter.for_experiment(session.experiment, session)
    to_sync = adapter.get_messages_to_sync_to_thread()
    assert to_sync == [
        {"role": "user", "content": "hello2"},
        {"role": "assistant", "content": "hello3"},
    ]


def _get_assistant_mocked_history_recording(session, get_attachments_return_value=None):
    adapter = AssistantAdapter.for_experiment(session.experiment, session)
    assistant = AssistantChat(adapter=adapter)
    adapter.save_message_to_history = Mock()
    adapter.get_attachments = lambda _type: get_attachments_return_value or []
    return assistant


def _create_thread_messages(
    assistant_id, run_id, thread_id, messages: list[dict[str, str]], annotations: list | None = None
):
    """
    Create a list of ThreadMessage objects from a list of message dictionaries:
    [
        {"user": "hello"},
        {"assistant": "hi"},
    ]
    """
    return [
        ThreadMessage(
            id="test",
            assistant_id=assistant_id,
            metadata={},
            created_at=0,
            content=[
                ImageFileContentBlock(
                    type="image_file",
                    image_file=ImageFile(file_id="test_file_id"),
                ),
                TextContentBlock(
                    text=Text(annotations=annotations if annotations else [], value=list(message.values())[0]),
                    type="text",
                ),
            ],
            object="thread.message",
            role=list(message)[0],
            run_id=run_id,
            thread_id=thread_id,
            status="completed",
        )
        for message in messages
    ]


def _create_run(
    assistant_id,
    thread_id,
    status: Literal[
        "queued", "in_progress", "requires_action", "cancelling", "cancelled", "failed", "completed", "expired"
    ] = "completed",
):
    run = Run(
        id="test",
        assistant_id=assistant_id,
        cancelled_at=None,
        completed_at=0,
        failed_at=None,
        last_error=None,
        metadata={},
        required_action=None,
        started_at=0,
        created_at=0,
        expires_at=0,
        instructions="",
        model="",
        object="thread.run",
        status=status,
        thread_id=thread_id,
        tools=[],
        parallel_tool_calls=False,
    )
    return run
