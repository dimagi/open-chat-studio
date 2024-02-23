from contextlib import nullcontext as does_not_raise
from typing import Literal
from unittest import mock
from unittest.mock import patch

import openai
import pytest
from openai.types.beta.threads import MessageContentText, Run, ThreadMessage
from openai.types.beta.threads.message_content_text import Text

from apps.chat.models import Chat
from apps.service_providers.llm_service.runnables import (
    AssistantExperimentRunnable,
    GenerationCancelled,
    GenerationError,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm

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


@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
@pytest.mark.django_db()
def test_assistant_conversation_input_formatting(create_and_run, retrieve_run, list_messages, session):
    session.experiment.input_formatter = "foo {input} bar"

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
    assert create_and_run.call_args.kwargs["thread"]["messages"][0]["content"] == "foo test bar"


@pytest.mark.django_db()
def test_assistant_runnable_raises_error(session):
    experiment = session.experiment

    error = openai.BadRequestError("test", response=mock.Mock(), body={})
    with mock_experiment_llm(experiment, [error]):
        assistant = AssistantExperimentRunnable(experiment=experiment, session=session)

        with pytest.raises(openai.BadRequestError):
            assistant.invoke("test")


@pytest.mark.django_db()
def test_assistant_runnable_handles_cancellation_status(session):
    experiment = session.experiment

    error = ValueError("unexpected status: cancelled")
    with mock_experiment_llm(experiment, [error]):
        assistant = AssistantExperimentRunnable(experiment=experiment, session=session)

        with pytest.raises(GenerationCancelled):
            assistant.invoke("test")


@pytest.mark.django_db()
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
def test_assistant_runnable_cancels_existing_run(responses, exception, output, session):
    experiment = session.experiment

    thread_id = "thread_abc"
    session.chat.set_metadata(session.chat.MetadataKeys.OPENAI_THREAD_ID, thread_id)
    assistant = AssistantExperimentRunnable(experiment=experiment, session=session)
    cancel_run = mock.Mock()
    assistant.__dict__["_cancel_run"] = cancel_run
    with mock_experiment_llm(experiment, responses):
        with exception:
            result = assistant.invoke("test")

    if output:
        assert result.output == "normal response"
        cancel_run.assert_called_once()


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
