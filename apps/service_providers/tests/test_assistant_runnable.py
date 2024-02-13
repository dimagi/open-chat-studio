from typing import Literal
from unittest.mock import patch

import pytest
from openai.types.beta.threads import MessageContentText, Run, ThreadMessage
from openai.types.beta.threads.message_content_text import Text

from apps.chat.models import Chat
from apps.service_providers.llm_service.runnables import AssistantExperimentRunnable
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

ASSISTANT_ID = "test_assistant_id"


@pytest.fixture()
def chat(team_with_users):
    return Chat.objects.create(team=team_with_users)


@pytest.fixture()
def session(chat):
    session = ExperimentSessionFactory(chat=chat)
    local_assistant = OpenAiAssistantFactory(assistant_id=ASSISTANT_ID)
    session.experiment.assistant = local_assistant
    return session


@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
@pytest.mark.django_db()
def test_assistant_conversation_new_chat(create_and_run, retrieve_run, list_messages, session):
    chat = session.chat
    assert chat.get_metadata(chat.MetadataKeys.OPENAI_THREAD_ID) is None

    thread_id = "test_thread_id"
    run = _create_run(ASSISTANT_ID, thread_id)
    create_and_run.return_value = run
    retrieve_run.return_value = run
    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": "ai response"}]
    )

    assistant = AssistantExperimentRunnable(experiment=session.experiment, session=session)
    result = assistant.invoke("test")
    assert result.output == "ai response"
    assert chat.get_metadata(chat.MetadataKeys.OPENAI_THREAD_ID) == thread_id


@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.messages.Messages.create")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.threads.runs.Runs.create")
@pytest.mark.django_db()
def test_assistant_conversation_existing_chat(create_run, retrieve_run, create_message, list_messages, session):
    thread_id = "test_thread_id"
    chat = session.chat
    chat.set_metadata(chat.MetadataKeys.OPENAI_THREAD_ID, thread_id)

    run = _create_run(ASSISTANT_ID, thread_id)
    create_run.return_value = run
    retrieve_run.return_value = run
    list_messages.return_value = _create_thread_messages(
        ASSISTANT_ID, run.id, thread_id, [{"assistant": "ai response"}]
    )

    assistant = AssistantExperimentRunnable(experiment=session.experiment, session=session)
    result = assistant.invoke("test")

    assert create_message.call_args.args == (thread_id,)
    assert create_run.call_args.args == (thread_id,)
    assert result.output == "ai response"


def _create_thread_messages(assistant_id, run_id, thread_id, messages: list[dict[str, str]]):
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
            content=[MessageContentText(text=Text(annotations=[], value=list(message.values())[0]), type="text")],
            file_ids=[],
            object="thread.message",
            role=list(message)[0],
            run_id=run_id,
            thread_id=thread_id,
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
        file_ids=[],
        instructions="",
        model="",
        object="thread.run",
        status=status,
        thread_id=thread_id,
        tools=[],
    )
    return run
