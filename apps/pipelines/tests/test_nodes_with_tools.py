from unittest.mock import Mock, patch

import pytest
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish

from apps.experiments.models import AgentTools
from apps.pipelines.nodes.base import PipelineState
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
    if not disabled_tools or disabled_tools == tools:
        # TODO: fix for case when all tools are disabled
        assert args[0] == {"content": "Hi there bot", "attachments": [], "instructions": ""}
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
@patch("apps.service_providers.llm_service.runnables.AgentLLMChat.invoke")
@patch("apps.service_providers.llm_service.runnables.SimpleLLMChat.invoke")
def test_llm_node(simple_invoke, agent_invoke, disabled_tools, provider, provider_model):
    simple_invoke.return_value = Mock(output="hello")
    agent_invoke.return_value = Mock(output="hello")
    pipeline = PipelineFactory()
    tools = [AgentTools.ONE_OFF_REMINDER, AgentTools.UPDATE_PARTICIPANT_DATA]
    node = llm_response_with_prompt_node(
        str(provider.id),
        str(provider_model.id),
        tools=tools,
        prompt="Be useful: {current_datetime} {participant_data}",
    )
    nodes = [start_node(), node, end_node()]
    runnable = create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Hi there bot"],
        experiment_session=ExperimentSessionFactory(),
        pipeline_version=pipeline.version_number,
    )
    output_state = runnable.invoke(state, config={"configurable": {"disabled_tools": disabled_tools}})
    assert output_state["messages"][-1] == "hello"
    if disabled_tools == tools:
        assert simple_invoke.call_count == 1
        assert not agent_invoke.called
    elif disabled_tools != tools:
        assert not simple_invoke.called
        assert agent_invoke.call_count == 1
