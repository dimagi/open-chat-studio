from typing import Any
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import ToolMessage, tool_call_chunk
from langchain_core.tools import BaseTool, Tool

from apps.chat.agent.openapi_tool import ToolArtifact
from apps.experiments.runnables import AgentLLMChat, ExperimentHistoryManager
from apps.service_providers.llm_service.adapters import ChatAdapter
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service


def _make_tool_for_testing(output: Any, **kwargs) -> BaseTool:
    return Tool(name="fake_tool", description="Tool for testing", func=Mock(return_value=output), **kwargs)


@pytest.fixture()
def fake_llm_service():
    ai_tool_response = AIMessageChunk(
        content="ai response",
        tool_call_chunks=[tool_call_chunk(name="fake_tool", args='{"arg1": "test arg"}', id="call1")],
    )
    return build_fake_llm_service(responses=[ai_tool_response, "ai response after tool"], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    session = ExperimentSessionFactory()
    session.experiment.get_llm_service = lambda *args, **kwargs: fake_llm_service
    return session


@pytest.mark.django_db()
def test_tool_response(session, fake_llm_service):
    tool = _make_tool_for_testing("test tool output")
    runnable = get_runnable(session, tool)
    result = runnable.invoke("test")

    assert result.output == "ai response after tool"
    assert tool.func.call_args == (("test arg",), {})
    assert len(fake_llm_service.llm.get_call_messages()) == 2
    last_message = fake_llm_service.llm.get_call_messages()[-1][-1]
    assert last_message == ToolMessage(
        content="test tool output", tool_call_id="call1", additional_kwargs={"name": "fake_tool"}
    )


@pytest.mark.django_db()
def test_tool_artifact_response(session, fake_llm_service):
    # mock the tool so that it returns an artifact. This should be ignored by a normal Chat model
    artifact = ToolArtifact(content=b"test artifact", name="test_artifact.txt", content_type="text/plain")
    tool = _make_tool_for_testing(("test tool output", artifact), response_format="content_and_artifact")
    runnable = get_runnable(session, tool)
    result = runnable.invoke("test")

    assert result.output == "ai response after tool"
    assert tool.func.call_args == (("test arg",), {})
    assert len(fake_llm_service.llm.get_call_messages()) == 2
    last_message = fake_llm_service.llm.get_call_messages()[-1][-1]
    assert last_message == ToolMessage(
        content="test tool output", tool_call_id="call1", additional_kwargs={"name": "fake_tool"}
    )


def get_runnable(session, tool):
    with patch("apps.service_providers.llm_service.adapters.get_tools") as get_tools:
        get_tools.return_value = [tool]
        history_manager = ExperimentHistoryManager.for_llm_chat(session, session.experiment, TracingService.empty())
        adapter = ChatAdapter.for_experiment(session.experiment, session)
        runnable = AgentLLMChat(adapter=adapter, history_manager=history_manager)
    return runnable
