from unittest import mock
from unittest.mock import Mock, patch

import pytest
from langchain_classic.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.runnables import ensure_config
from langchain_core.tools import Tool

from apps.chat.agent.openapi_tool import ToolArtifact
from apps.experiments.models import AgentTools
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.llm_node import _get_configured_tools
from apps.pipelines.nodes.nodes import (
    LLMResponseWithPrompt,
)
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.pipelines.tests.utils import (
    assistant_node,
    create_pipeline_model,
    create_runnable,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.langchain import (
    build_fake_llm_service,
)
from apps.utils.pytest import django_db_transactional, django_db_with_data


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
    pipeline = create_pipeline_model([start_node(), node_data, end_node()])
    django_node = pipeline.node_set.get(flow_id=node_data["id"])
    node = LLMResponseWithPrompt.model_validate(
        {**node_data["params"], "node_id": node_data["id"], "django_node": django_node}
    )
    node._config = ensure_config({"configurable": {"disabled_tools": disabled_tools}})
    tools = _get_configured_tools(node, ExperimentSessionFactory(), ToolCallbacks())
    tool_names = {getattr(tool, "name", "") for tool in tools}
    assert not set(disabled_tools) & tool_names


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_tool_call_with_annotated_inputs(get_llm_service, provider, provider_model):
    service = build_fake_llm_service(
        responses=[
            _tool_call(AgentTools.UPDATE_PARTICIPANT_DATA, {"key": "test", "value": "123"}),
            _tool_call(AgentTools.APPEND_TO_PARTICIPANT_DATA, {"key": "test", "value": "next"}),
            "123",
        ],
        token_counts=[0],
    )
    get_llm_service.return_value = service
    nodes = [
        start_node(),
        llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            prompt="Be helpful. {participant_data}",
            name="llm1",
            tools=[AgentTools.UPDATE_PARTICIPANT_DATA, AgentTools.APPEND_TO_PARTICIPANT_DATA],
        ),
        end_node(),
    ]
    pipeline = PipelineFactory()
    session = ExperimentSessionFactory()
    graph = create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Repeat exactly: 123"],
        experiment_session=session,
        participant_data={"test": "abc", "other": "xyz"},
    )
    output = graph.invoke(state)
    assert output["messages"][-1] == "123"
    assert output["participant_data"] == {"test": ["123", "next"], "other": "xyz"}


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.llm_node._get_configured_tools")
def test_tool_artifact_response(get_configured_tools, get_llm_service, provider, provider_model):
    artifact = ToolArtifact(content=b"test artifact", name="test_artifact.txt", content_type="text/plain")
    tool = Tool(
        name=AgentTools.UPDATE_PARTICIPANT_DATA,
        description="Tool for testing",
        func=Mock(return_value=("test tool output", artifact)),
        response_format="content_and_artifact",
    )

    service = build_fake_llm_service(
        responses=[
            _tool_call(AgentTools.UPDATE_PARTICIPANT_DATA, {"arg1": "test arg"}),
            "ai response after tool",
        ],
        token_counts=[0],
    )
    get_configured_tools.return_value = [tool]

    get_llm_service.return_value = service
    nodes = [
        start_node(),
        llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            prompt="Be helpful. {participant_data}",
            name="llm1",
            tools=[AgentTools.UPDATE_PARTICIPANT_DATA],
        ),
        end_node(),
    ]
    pipeline = PipelineFactory()
    session = ExperimentSessionFactory()
    graph = create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Repeat exactly: 123"],
        experiment_session=session,
        participant_data={"test": "abc", "other": "xyz"},
    )
    output = graph.invoke(state)
    assert output["messages"][-1] == "ai response after tool"
    assert tool.func.call_args == (("test arg",), {})
    assert len(service.llm.get_call_messages()) == 2
    last_message = service.llm.get_call_messages()[-1][-1]
    assert last_message.content == "test tool output"
    assert last_message.artifact == artifact


def _tool_call(name, args):
    return AIMessage(tool_calls=[ToolCall(name=name, args=args, id="123")], content="")
