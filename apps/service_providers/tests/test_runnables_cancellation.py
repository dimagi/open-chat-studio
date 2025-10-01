from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessageChunk

from apps.chat.agent.tools import OneOffReminderTool
from apps.chat.models import Chat
from apps.experiments.models import AgentTools
from apps.service_providers.llm_service.adapters import ChatAdapter
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.llm_service.runnables import (
    AgentLLMChat,
    ChainOutput,
    GenerationCancelled,
    SimpleLLMChat,
)
from apps.service_providers.tracing import TracingService
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def fake_llm_service():
    return build_fake_llm_service(responses=[["This", " is", " a", " test", " message"]], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    chat = Chat()
    chat.get_langchain_messages_until_marker = lambda marker: []
    chat.refresh_from_db = lambda *args, **kwargs: None
    chat.save = lambda: None
    session = ExperimentSessionFactory.build(chat=chat)
    session.experiment.assistant = OpenAiAssistantFactory.build()
    session.experiment.get_llm_service = lambda: fake_llm_service
    session.experiment.tools = [AgentTools.MOVE_SCHEDULED_MESSAGE_DATE]
    session.get_participant_data = lambda *args, **kwargs: ""
    session.participant_data_from_experiment = {}
    return session


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.adapters.get_tools")
@patch("apps.chat.models.Chat.attach_files", new=Mock())
def test_simple_runnable_cancellation(get_tools, session, fake_llm_service):
    get_tools.return_value = []
    runnable = _get_runnable_with_mocked_history(session, SimpleLLMChat)
    _test_runnable(runnable, session, "This is")


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.adapters.get_tools")
@patch("apps.chat.models.Chat.attach_files", new=Mock())
def test_agent_runnable_cancellation(get_tools, session, fake_llm_service):
    get_tools.return_value = [OneOffReminderTool(experiment_session=session)]
    runnable = _get_runnable_with_mocked_history(session, AgentLLMChat)

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


def _get_runnable_with_mocked_history(session, runnable_cls):
    adapter = ChatAdapter.for_experiment(session.experiment, session)
    history_manager = ExperimentHistoryManager.for_llm_chat(
        session=session, experiment=session.experiment, trace_service=TracingService.empty()
    )
    runnable = runnable_cls(adapter=adapter, history_manager=history_manager, check_every_ms=0)
    history_manager.add_messages_to_history = Mock()
    return runnable
