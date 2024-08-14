from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessageChunk

from apps.chat.agent.tools import OneOffReminderTool
from apps.chat.models import Chat
from apps.experiments.models import AgentTools
from apps.service_providers.llm_service.runnables import (
    AgentExperimentRunnable,
    ChainOutput,
    GenerationCancelled,
    SimpleExperimentRunnable,
)
from apps.service_providers.llm_service.state import ChatExperimentState
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def fake_llm_service():
    return build_fake_llm_service(responses=[["This", " is", " a", " test", " message"]], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    chat = Chat()
    chat.get_langchain_messages_until_summary = lambda: []
    chat.refresh_from_db = lambda *args, **kwargs: None
    chat.save = lambda: None
    session = ExperimentSessionFactory.build(chat=chat)
    session.experiment.get_llm_service = lambda: fake_llm_service
    session.experiment.tools = [AgentTools.MOVE_SCHEDULED_MESSAGE_DATE]
    session.get_participant_data = lambda *args, **kwargs: ""
    return session


@pytest.mark.django_db()
def test_simple_runnable_cancellation(session, fake_llm_service):
    runnable = _get_assistant_mocked_history_recording(session, SimpleExperimentRunnable)
    _test_runnable(runnable, session, "This is")


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.state.get_tools")
def test_agent_runnable_cancellation(get_tools, session, fake_llm_service):
    get_tools.return_value = [OneOffReminderTool(experiment_session=session)]
    runnable = _get_assistant_mocked_history_recording(session, AgentExperimentRunnable)

    fake_llm_service.llm.responses = [
        AIMessageChunk(
            content="call tool",
            additional_kwargs={"tool_calls": [{"function": {"name": "foo", "arguments": "{}"}, "id": "1"}]},
        ),
        ["This is a test message"],
    ]
    _test_runnable(runnable, session, "")


def _test_runnable(runnable, session, expected_output):
    original_build = runnable._build_chain

    def _new_build():
        chain = original_build()
        orig_stream = chain.stream

        def _stream(*args, **kwargs):
            """Simulate a cancellation after the 2nd token."""
            for i, token in enumerate(orig_stream(*args, **kwargs)):
                if i == 1:
                    session.chat.metadata = {"cancelled": True}
                yield token

        chain.__dict__["stream"] = _stream
        return chain

    runnable.__dict__["_build_chain"] = _new_build

    with pytest.raises(GenerationCancelled) as exc_info:
        runnable.invoke("hi")
    assert exc_info.value.output == ChainOutput(output=expected_output, prompt_tokens=30, completion_tokens=20)


def _get_assistant_mocked_history_recording(session, cls):
    state = ChatExperimentState(session.experiment, session)
    assistant = cls(state=state, check_every_ms=0)
    state.save_message_to_history = Mock()
    return assistant
