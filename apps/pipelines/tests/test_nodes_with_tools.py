from unittest.mock import Mock, patch

import pytest
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain_core.runnables import ensure_config

from apps.experiments.models import AgentTools
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.llm_node import _get_configured_tools
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.pipelines.tests.utils import (
    assistant_node,
    create_runnable,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.pytest import django_db_transactional


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@pytest.fixture()
def provider_model():
    return LlmProviderModelFactory()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "disabled_tools",
    [
        [],
        [AgentTools.ONE_OFF_REMINDER],
        [AgentTools.ONE_OFF_REMINDER, AgentTools.UPDATE_PARTICIPANT_DATA],
    ],
)
@patch(
    "apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations",
    Mock(return_value=("hello", [])),
)
@patch("apps.service_providers.llm_service.main.OpenAIAssistantRunnable.invoke")
def test_assistant_node(patched_invoke, disabled_tools):
    patched_invoke.return_value = OpenAIAssistantFinish(
        return_values={
            "output": "hi",
            "thread_id": "thread_id",
            "run_id": "run_id",
        },
        log="",
        run_id="run_id",
        thread_id="thread_id",
    )
    pipeline = PipelineFactory()
    tools = [AgentTools.ONE_OFF_REMINDER, AgentTools.UPDATE_PARTICIPANT_DATA]
    assistant = OpenAiAssistantFactory(tools=tools)
    nodes = [start_node(), assistant_node(str(assistant.id)), end_node()]
    runnable = create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Hi there bot"],
        experiment_session=ExperimentSessionFactory(team=assistant.team),
    )
    output_state = runnable.invoke(state, config={"configurable": {"disabled_tools": disabled_tools}})
    assert output_state["messages"][-1] == "hello"
    args = patched_invoke.call_args[0]
    assert patched_invoke.call_count == 1
    if not disabled_tools:
        assert args[0] == {"content": "Hi there bot", "attachments": [], "instructions": ""}
    elif disabled_tools == tools:
        assert args[0] == {"content": "Hi there bot", "attachments": [], "instructions": "", "tools": []}
    elif disabled_tools != tools:
        assert patched_invoke.call_count == 1
        assert len(args[0]["tools"]) == 1
        assert args[0]["tools"][0]["function"]["name"] == AgentTools.UPDATE_PARTICIPANT_DATA


@django_db_transactional()
@pytest.mark.parametrize(
    "disabled_tools",
    [
        [],
        [AgentTools.ONE_OFF_REMINDER],
        [AgentTools.ONE_OFF_REMINDER, AgentTools.UPDATE_PARTICIPANT_DATA],
    ],
)
def test_tool_filtering(disabled_tools, provider, provider_model):
    tools = [AgentTools.ONE_OFF_REMINDER, AgentTools.UPDATE_PARTICIPANT_DATA]
    node_data = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        tools=tools,
        prompt="Be useful: {current_datetime} {participant_data}",
    )
    node = LLMResponseWithPrompt.model_validate(node_data)
    node._config = ensure_config({"configurable": {"disabled_tools": disabled_tools}})
    tools = _get_configured_tools(node, ExperimentSessionFactory(), ToolCallbacks())
    tool_names = {getattr(tool, "name", "") for tool in tools}
    assert not disabled_tools & tool_names
