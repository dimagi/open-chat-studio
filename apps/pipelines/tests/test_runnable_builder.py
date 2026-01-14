from contextlib import contextmanager
from typing import Literal
from unittest import mock
from unittest.mock import Mock, patch

import pytest
from django.core import mail
from django.test import override_settings
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCall, ToolCallChunk
from langchain_openai.chat_models.base import OpenAIRefusalError
from pydantic import Field, create_model
from pydantic import ValidationError as PydanticValidationError

from apps.annotations.models import TagCategories
from apps.channels.datamodels import Attachment
from apps.experiments.models import AgentTools
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.nodes.base import Intents, PipelineState, merge_dict_values_as_lists
from apps.pipelines.nodes.nodes import (
    EndNode,
    LLMResponseWithPrompt,
    Passthrough,
    RouterNode,
    StartNode,
    StaticRouterNode,
)
from apps.pipelines.tests.utils import (
    assistant_node,
    boolean_node,
    code_node,
    create_runnable,
    email_node,
    end_node,
    extract_participant_data_node,
    extract_structured_data_node,
    llm_response_node,
    llm_response_with_prompt_node,
    passthrough_node,
    render_template_node,
    router_node,
    start_node,
    state_key_router_node,
)
from apps.service_providers.llm_service.runnables import ChainOutput
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.langchain import (
    FakeLlmEcho,
    FakeLlmService,
    FakeLlmSimpleTokenCount,
    FakeTokenCounter,
    build_fake_llm_echo_service,
    build_fake_llm_service,
)
from apps.utils.pytest import django_db_with_data


# Helper class used by router node tests
class RefusingFakeLlmEcho(FakeLlmEcho):
    def invoke(self, *args, **kwargs):
        raise OpenAIRefusalError("Refused by OpenAI")


class PydanticValidationErrorLlmEcho(FakeLlmEcho):
    def invoke(self, *args, **kwargs):
        raise PydanticValidationError("Invalid data structure", [])


class StructuredOutputValidationErrorLlmEcho(FakeLlmEcho):
    def invoke(self, *args, **kwargs):
        raise StructuredOutputValidationError(
            tool_name="test_tool",
            source=Exception("Unable to parse json"),
            ai_message=AIMessage(content=""),
        )


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@pytest.fixture()
def provider_model():
    return LlmProviderModelFactory()


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def source_material():
    return SourceMaterialFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


class TestEmailPipeline:
    """Tests for email-related pipeline functionality"""

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_full_email_sending_pipeline(self, get_llm_service, provider, provider_model, pipeline):
        service = build_fake_llm_service(responses=['{"summary": "Ice is cold"}'], token_counts=[0])
        get_llm_service.return_value = service

        nodes = [
            start_node(),
            render_template_node(),
            llm_response_with_prompt_node(str(provider.id), str(provider_model.id)),
            email_node(),
            end_node(),
        ]

        state = PipelineState(
            messages=["Ice is not a liquid. When it is melted it turns into water."],
            experiment_session=ExperimentSessionFactory(),
        )
        create_runnable(pipeline, nodes).invoke(state)
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "This is an interesting email"
        assert mail.outbox[0].to == ["test@example.com"]

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @django_db_with_data()
    def test_send_email(self, pipeline):
        nodes = [start_node(), email_node(), end_node()]
        create_runnable(pipeline, nodes).invoke(PipelineState(messages=["A cool message"]))
        assert len(mail.outbox) == 1
        assert mail.outbox[0].body == "A cool message"
        assert mail.outbox[0].subject == "This is an interesting email"
        assert mail.outbox[0].to == ["test@example.com"]


class TestLLMResponse:
    """Tests for LLM response nodes"""

    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_llm_response(self, get_llm_service, provider, provider_model, pipeline):
        service = build_fake_llm_service(responses=["123"], token_counts=[0])
        get_llm_service.return_value = service
        nodes = [
            start_node(),
            llm_response_node(str(provider.id), str(provider_model.id)),
            end_node(),
        ]
        assert (
            create_runnable(pipeline, nodes).invoke(PipelineState(messages=["Repeat exactly: 123"]))["messages"][-1]
            == "123"
        )

    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_llm_with_prompt_response(
        self, get_llm_service, provider, provider_model, pipeline, source_material, experiment_session
    ):
        service = build_fake_llm_echo_service()
        get_llm_service.return_value = service

        user_input = "The User Input"
        nodes = [
            start_node(),
            llm_response_with_prompt_node(
                str(provider.id),
                str(provider_model.id),
                source_material_id=str(source_material.id),
                prompt="Node 1: Use this {source_material} to answer questions about {participant_data}.",
                name="llm1",
            ),
            llm_response_with_prompt_node(
                str(provider.id),
                str(provider_model.id),
                prompt="Node 2: {temp_state.temp_key} {session_state.session_key}",
                name="llm2",
            ),
            end_node(),
        ]
        participant_data = {"name": "A"}
        output = create_runnable(pipeline, nodes).invoke(
            PipelineState(
                messages=[user_input],
                experiment_session=experiment_session,
                temp_state={"temp_key": "temp_value"},
                participant_data=participant_data,
                session_state={"session_key": "session_value"},
            )
        )["messages"][-1]
        expected_output = (
            f"Node 2: temp_value session_value Node 1: Use this {source_material.material} to answer questions "
            f"about {participant_data}. {user_input}"
        )
        assert output == expected_output

    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_end_session_tool(self, get_llm_service, provider, provider_model, pipeline, experiment_session):
        def _tool_call():
            return AIMessageChunk(
                tool_call_chunks=[ToolCallChunk(name=AgentTools.END_SESSION, id="123", args="")], content=""
            )

        service = build_fake_llm_service(
            responses=[_tool_call(), "Done"],
            token_counts=[0],
        )
        get_llm_service.return_value = service
        start = start_node()
        llm = llm_response_with_prompt_node(str(provider.id), str(provider_model.id), tools=[AgentTools.END_SESSION])
        end = end_node()
        nodes = [start, llm, end]
        edges = [
            {"id": "start -> llm", "source": start["id"], "target": llm["id"]},
            {"id": "llm -> end", "source": llm["id"], "target": end["id"]},
        ]
        runnable = create_runnable(pipeline, nodes, edges)

        output = runnable.invoke(PipelineState(messages=["a"], experiment_session=experiment_session))
        assert output["intents"] == [Intents.END_SESSION]

    @django_db_with_data()
    def test_llm_model_parameters_with_none_value(self, provider, provider_model):
        """There was a bug where llm_model_parameters being `None` caused validation to fail, because it didn't default
        to a dictionary correctly
        """
        data = llm_response_with_prompt_node(
            provider_id=str(provider.id),
            provider_model_id=str(provider_model.id),
            llm_model_parameters=None,
        )
        params = data["params"] | {"django_node": 1, "node_id": "1"}
        validated = LLMResponseWithPrompt.model_validate(params)
        assert validated.llm_model_parameters == {"temperature": 0.7}


class TestTemplateRendering:
    """Tests for template rendering nodes"""

    @django_db_with_data()
    def test_render_template(self, pipeline):
        nodes = [
            start_node(),
            render_template_node("{{ input }} is cool"),
            end_node(),
        ]

        result = create_runnable(pipeline, nodes).invoke(PipelineState(messages=["Cycling"]))
        assert result["messages"][-1] == "Cycling is cool"


class TestConditionalNode:
    """Tests for conditional/boolean nodes"""

    @django_db_with_data()
    def test_conditional_node(self, pipeline, experiment_session):
        start = start_node()
        boolean = boolean_node(name="boolean")
        template_true = render_template_node("said hello", name="T-true")
        template_false = render_template_node("didn't say hello, said {{ input }}", name="T-false")
        end = end_node()
        nodes = [
            start,
            boolean,
            template_true,
            template_false,
            end,
        ]
        edges = [
            {"id": "start -> boolean", "source": start["id"], "target": boolean["id"]},
            {
                "id": "Boolean -> True",
                "source": boolean["id"],
                "target": template_true["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "Boolean -> False",
                "source": boolean["id"],
                "target": template_false["id"],
                "sourceHandle": "output_1",
            },
            {
                "id": "False -> End",
                "source": template_false["id"],
                "target": end["id"],
            },
            {
                "id": "True -> End",
                "source": template_true["id"],
                "target": end["id"],
            },
        ]
        runnable = create_runnable(pipeline, nodes, edges)
        output = runnable.invoke(PipelineState(messages=["hello"], experiment_session=experiment_session))
        assert output["messages"][-1] == "said hello"
        assert output["outputs"] == {
            "start": {"message": "hello", "node_id": start["id"]},
            "boolean": {"route": "true", "message": "hello", "output_handle": "output_0", "node_id": boolean["id"]},
            "T-true": {"message": "said hello", "node_id": template_true["id"]},
            "end": {"message": "said hello", "node_id": end["id"]},
        }

        output = runnable.invoke(PipelineState(messages=["bad"], experiment_session=experiment_session))
        assert output["messages"][-1] == "didn't say hello, said bad"
        assert output["outputs"] == {
            "start": {"message": "bad", "node_id": start["id"]},
            "boolean": {"route": "false", "message": "bad", "output_handle": "output_1", "node_id": boolean["id"]},
            "T-false": {"message": "didn't say hello, said bad", "node_id": template_false["id"]},
            "end": {"message": "didn't say hello, said bad", "node_id": end["id"]},
        }


class TestRouterNode:
    """Tests for router nodes (LLM-based routing)"""

    @pytest.mark.django_db()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    @mock.patch("apps.pipelines.nodes.nodes.create_agent")
    def test_router_node_prompt(self, create_agent_mock, get_llm_service, provider, provider_model, experiment_session):
        service = build_fake_llm_echo_service()
        get_llm_service.return_value = service

        # Create a mock agent that returns structured output
        mock_agent = mock.Mock()
        RouterOutput = create_model(
            "RouterOutput", route=(Literal["A"], Field(description="Selected routing destination"))
        )
        mock_agent.invoke.return_value = {"structured_response": RouterOutput(route="A")}
        create_agent_mock.return_value = mock_agent

        node = RouterNode(
            node_id="test",
            django_node=None,
            name="test router",
            prompt="PD: {participant_data}",
            keywords=["A"],
            llm_provider_id=provider.id,
            llm_provider_model_id=provider_model.id,
        )
        participant_data = {"participant_data": "b"}
        node._process_conditional(
            PipelineState(
                outputs={"123": {"message": "a"}},
                messages=["a"],
                experiment_session=experiment_session,
                node_inputs=["a"],
                last_node_input="a",
                participant_data=participant_data,
            ),
        )

        # Verify that create_agent was called with the correct system prompt containing participant data
        assert create_agent_mock.called
        call_kwargs = create_agent_mock.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        expected_pd = {"name": experiment_session.participant.name} | participant_data
        assert str(expected_pd) in system_prompt.content

    @django_db_with_data()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_router_node(self, get_llm_service, provider, provider_model, pipeline, experiment_session):
        def _tool_call(route):
            return AIMessage(tool_calls=[ToolCall(name="RouterOutput", args={"route": route}, id="123")], content=route)

        service = build_fake_llm_service(
            responses=[
                # "a" is not a valid keyword
                _tool_call("invalid_keyword"),
                # This second response is the LLM fixing the fact that the first response did not match any keyword
                _tool_call("invalid_keyword"),
                _tool_call("a"),
                _tool_call("a"),
                _tool_call("b"),
                _tool_call("c"),
                _tool_call("d"),
                _tool_call("z"),
            ],
            token_counts=[0],
        )
        get_llm_service.return_value = service
        start = start_node()
        router = router_node(str(provider.id), str(provider_model.id), keywords=["a", "b", "c", "d"])
        template_a = render_template_node("Template A: {{ input }}")
        template_b = render_template_node("Template B: {{ input }}")
        template_c = render_template_node("Template C: {{ input }}")
        template_d = render_template_node("Template D: {{ input }}")
        end = end_node()
        nodes = [start, router, template_a, template_b, template_c, template_d, end]
        edges = [
            {"id": "start -> router", "source": start["id"], "target": router["id"]},
            {
                "id": "RouterNode -> A",
                "source": router["id"],
                "target": template_a["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "RouterNode -> B",
                "source": router["id"],
                "target": template_b["id"],
                "sourceHandle": "output_1",
            },
            {
                "id": "RouterNode -> C",
                "source": router["id"],
                "target": template_c["id"],
                "sourceHandle": "output_2",
            },
            {
                "id": "RouterNode -> D",
                "source": router["id"],
                "target": template_d["id"],
                "sourceHandle": "output_3",
            },
            {
                "id": "A -> END",
                "source": template_a["id"],
                "target": end["id"],
            },
            {
                "id": "B -> END",
                "source": template_b["id"],
                "target": end["id"],
            },
            {
                "id": "C -> END",
                "source": template_c["id"],
                "target": end["id"],
            },
            {
                "id": "D -> END",
                "source": template_d["id"],
                "target": end["id"],
            },
        ]
        runnable = create_runnable(pipeline, nodes, edges)

        output = runnable.invoke(PipelineState(messages=["a"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template A: a"
        output = runnable.invoke(PipelineState(messages=["A"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template A: A"
        output = runnable.invoke(PipelineState(messages=["b"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template B: b"
        output = runnable.invoke(PipelineState(messages=["c"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template C: c"
        output = runnable.invoke(PipelineState(messages=["d"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template D: d"
        output = runnable.invoke(PipelineState(messages=["z"], experiment_session=experiment_session))
        assert output["messages"][-1] == "Template A: z"

    @pytest.mark.django_db()
    def test_router_node_output_structure(self, provider, provider_model, pipeline, experiment_session):
        service = build_fake_llm_echo_service()
        with mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service):
            node_id = "123"
            node = RouterNode(
                node_id=node_id,
                django_node=None,
                name="test_router",
                prompt="PD: {participant_data}",
                keywords=["a"],
                llm_provider_id=provider.id,
                llm_provider_model_id=provider_model.id,
            )
            state = PipelineState(
                outputs={"prev_node": {"message": "hello world", "node_id": "prev_node"}},
                messages=["hello world"],
                experiment_session=experiment_session,
                temp_state={"user_input": "hello world", "outputs": {}},
                path=[("", "prev_node", [node_id])],
            )
            with mock.patch.object(node, "_process_conditional", return_value=("a", True)):
                edge_map = {"a": "next_node_a", "b": "next_node_b"}
                incoming_edges = ["prev_node"]
                router_func = node.build_router_function(edge_map, incoming_edges)
                command = router_func(state, {})

                output_state = command.update

                assert node.name in output_state["outputs"]
                assert "route" in output_state["outputs"][node.name]
                assert "message" in output_state["outputs"][node.name]
                assert output_state["outputs"][node.name]["route"] == "a"
                assert output_state["outputs"][node.name]["message"] == "hello world"
                assert command.goto == ["next_node_a"]

    @pytest.mark.django_db()
    @mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
    @pytest.mark.parametrize(
        "LLMClass", [RefusingFakeLlmEcho, PydanticValidationErrorLlmEcho, StructuredOutputValidationErrorLlmEcho]
    )
    def test_router_node_uses_default_keyword_on_error(
        self, get_llm_service, LLMClass, provider, provider_model, experiment_session
    ):
        refusing_llm = LLMClass(include_system_message=True)
        service = FakeLlmService(llm=refusing_llm, token_counter=FakeTokenCounter(token_counts=[0]))
        get_llm_service.return_value = service
        node = RouterNode(
            node_id="test",
            django_node=None,
            name="test router",
            prompt="PD: {participant_data}",
            keywords=["default", "a", "b"],
            llm_provider_id=provider.id,
            llm_provider_model_id=provider_model.id,
        )
        node.default_keyword_index = 0
        state = PipelineState(
            outputs={"123": {"message": "a"}},
            messages=["a"],
            experiment_session=experiment_session,
            node_inputs=["a"],
            last_node_input="a",
        )

        keyword, is_default_keyword = node._process_conditional(state)
        assert keyword == "default"
        assert is_default_keyword


class TestStaticRouterNode:
    """Tests for static router nodes (state-based routing without LLM)"""

    @django_db_with_data()
    def test_static_router_temp_state(self, pipeline, experiment_session):
        # The static router will switch based on a state key, and pass its input through

        code_set = """
def main(input, **kwargs):
    if "go to first" in input.lower():
        set_temp_state_key("route_to", "first")
    elif "go to second" in input.lower():
        set_temp_state_key("route_to", "second")
    return input
"""
        start = start_node()
        code = code_node(code_set)
        router = state_key_router_node(
            "route_to", ["first", "second"], data_source=StaticRouterNode.DataSource.temp_state
        )
        template_a = render_template_node("A {{ input }}")
        template_b = render_template_node("B {{ input }}")
        end = end_node()
        nodes = [start, code, router, template_a, template_b, end]
        edges = [
            {"id": "start -> code", "source": start["id"], "target": code["id"]},
            {"id": "code -> router", "source": code["id"], "target": router["id"]},
            {
                "id": "router -> A",
                "source": router["id"],
                "target": template_a["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "router -> B",
                "source": router["id"],
                "target": template_b["id"],
                "sourceHandle": "output_1",
            },
            {"id": "A -> end", "source": template_a["id"], "target": end["id"]},
            {"id": "B -> end", "source": template_b["id"], "target": end["id"]},
        ]
        runnable = create_runnable(pipeline, nodes, edges)
        output = runnable.invoke(PipelineState(messages=["Go to FIRST"], experiment_session=experiment_session))
        assert output["messages"][-1] == "A Go to FIRST"

        output = runnable.invoke(PipelineState(messages=["Go to Second"], experiment_session=experiment_session))
        assert output["messages"][-1] == "B Go to Second"

        # default route
        output = runnable.invoke(PipelineState(messages=["Go to Third"], experiment_session=experiment_session))
        assert output["messages"][-1] == "A Go to Third"

    @django_db_with_data()
    def test_static_router_case_sensitive(self, pipeline, experiment_session):
        start = start_node()
        router = state_key_router_node(
            "route_to", ["first", "SECOND", "third"], data_source=StaticRouterNode.DataSource.temp_state
        )
        template_a = render_template_node("A")
        template_b = render_template_node("B")
        template_c = render_template_node("C")
        end = end_node()
        nodes = [start, router, template_a, template_b, template_c, end]
        edges = [
            {"id": "start -> code", "source": start["id"], "target": router["id"]},
            {
                "id": "router -> A",
                "source": router["id"],
                "target": template_a["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "router -> B",
                "source": router["id"],
                "target": template_b["id"],
                "sourceHandle": "output_1",
            },
            {
                "id": "router -> C",
                "source": router["id"],
                "target": template_c["id"],
                "sourceHandle": "output_2",
            },
            {"id": "A -> end", "source": template_a["id"], "target": end["id"]},
            {"id": "B -> end", "source": template_b["id"], "target": end["id"]},
            {"id": "C -> end", "source": template_c["id"], "target": end["id"]},
        ]
        runnable = create_runnable(pipeline, nodes, edges)

        def _check_match(route_to, expected):
            output = runnable.invoke(
                PipelineState(messages=[""], experiment_session=experiment_session, temp_state={"route_to": route_to})
            )
            assert output["messages"][-1] == expected

        # Check that matches are not case-sensitive in either direction
        _check_match("SECOND", "B")
        _check_match("second", "B")
        _check_match("third", "C")
        _check_match("THIRD", "C")

    @pytest.mark.django_db()
    def test_router_sets_tags_correctly(self, pipeline, experiment_session):
        start = start_node()
        router = state_key_router_node(
            "route_to",
            ["first", "second"],
            data_source=StaticRouterNode.DataSource.temp_state,
            tag_output=True,
            name="static router",
        )
        template_a = render_template_node("A")
        template_b = render_template_node("B")
        end = end_node()

        nodes = [start, router, template_a, template_b, end]
        edges = [
            {"id": "start -> router", "source": start["id"], "target": router["id"]},
            {
                "id": "router -> A",
                "source": router["id"],
                "target": template_a["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "router -> B",
                "source": router["id"],
                "target": template_b["id"],
                "sourceHandle": "output_1",
            },
            {"id": "A -> end", "source": template_a["id"], "target": end["id"]},
            {"id": "B -> end", "source": template_b["id"], "target": end["id"]},
        ]
        runnable = create_runnable(pipeline, nodes, edges)

        def _check_routing_and_tags(route_to, expected_tag):
            output = runnable.invoke(
                PipelineState(
                    messages=["Test message"], experiment_session=experiment_session, temp_state={"route_to": route_to}
                )
            )
            assert output["output_message_tags"] == [(f"static router:{expected_tag}", TagCategories.BOT_RESPONSE)]

        _check_routing_and_tags("first", "first")
        _check_routing_and_tags("second", "second")

    @django_db_with_data()
    @pytest.mark.parametrize(
        "data_source", [StaticRouterNode.DataSource.participant_data, StaticRouterNode.DataSource.session_state]
    )
    def test_static_router_participant_data(self, data_source, pipeline, experiment_session):
        start = start_node()
        router = state_key_router_node("route_to", ["first", "second"], data_source=data_source)
        template_a = render_template_node("A {{ input }}")
        template_b = render_template_node("B {{ input }}")
        end = end_node()
        nodes = [start, router, template_a, template_b, end]
        edges = [
            {"id": "start -> router", "source": start["id"], "target": router["id"]},
            {
                "id": "router -> A",
                "source": router["id"],
                "target": template_a["id"],
                "sourceHandle": "output_0",
            },
            {
                "id": "router -> B",
                "source": router["id"],
                "target": template_b["id"],
                "sourceHandle": "output_1",
            },
            {"id": "A -> end", "source": template_a["id"], "target": end["id"]},
            {"id": "B -> end", "source": template_b["id"], "target": end["id"]},
        ]
        runnable = create_runnable(pipeline, nodes, edges)

        def _get_state(route):
            state = PipelineState(messages=["Hi"], experiment_session=experiment_session)
            if data_source == StaticRouterNode.DataSource.participant_data:
                state["participant_data"] = route
            else:
                state["session_state"] = route
            return state

        output = runnable.invoke(_get_state({"route_to": "first"}))
        assert output["messages"][-1] == "A Hi"

        output = runnable.invoke(_get_state({"route_to": "second"}))
        assert output["messages"][-1] == "B Hi"

        # default route
        output = runnable.invoke(_get_state({}))
        assert output["messages"][-1] == "A Hi"


class TestCodeNode:
    """Tests for code execution nodes"""

    @django_db_with_data()
    def test_attachments_in_code_node(self, pipeline, experiment_session):
        code_set = """
def main(input, **kwargs):
    attachments = get_temp_state_key("attachments")
    # TODO: tracing
    # kwargs["logger"].info([att.model_dump() for att in attachments])
    return ",".join([att.name for att in attachments])
"""
        start = start_node()
        code = code_node(code_set)
        end = end_node()
        nodes = [start, code, end]
        runnable = create_runnable(pipeline, nodes)
        attachments = [
            Attachment(
                file_id=123, type="code_interpreter", name="test.py", size=10, download_link="http://localhost:8000"
            ),
            Attachment(file_id=456, type="file_search", name="blog.md", size=20, download_link="http://localhost:8000"),
        ]
        serialized_attachments = [att.model_dump() for att in attachments]
        output = runnable.invoke(
            PipelineState(
                messages=["log attachments"], experiment_session=experiment_session, attachments=serialized_attachments
            ),
        )
        assert output["messages"][-1] == "test.py,blog.md"


class TestDataExtraction:
    """Tests for data extraction nodes"""

    @contextmanager
    def extract_structured_data_pipeline(self, provider, provider_model, pipeline, llm=None):
        tool_response = AIMessage(
            tool_calls=[ToolCall(name="CustomModel", args={"name": "John"}, id="123")], content="Hi"
        )
        service = build_fake_llm_service(responses=[tool_response], token_counts=[0], fake_llm=llm)

        with (
            mock.patch(
                "apps.service_providers.models.LlmProvider.get_llm_service",
                return_value=service,
            ),
        ):
            nodes = [
                start_node(),
                extract_structured_data_node(
                    str(provider.id), str(provider_model.id), '{"name": "the name of the user"}'
                ),
                end_node(),
            ]
            runnable = create_runnable(pipeline, nodes)
            yield runnable

    @django_db_with_data()
    def test_extract_structured_data_no_chunking(self, provider, provider_model, pipeline):
        session = ExperimentSessionFactory()

        with self.extract_structured_data_pipeline(provider, provider_model, pipeline) as graph:
            state = PipelineState(
                messages=["ai: hi user\nhuman: hi there I am John"],
                experiment_session=session,
            )
            assert graph.invoke(state)["messages"][-1] == '{"name": "John"}'

    @django_db_with_data()
    def test_extract_structured_data_with_chunking(self, provider, provider_model, pipeline):
        session = ExperimentSessionFactory()
        llm = FakeLlmSimpleTokenCount(
            responses=[
                # the first chunk sees nothing of value
                AIMessage(tool_calls=[ToolCall(name="CustomModel", args={"name": None}, id="123")], content="Hi"),
                # the second chunk message sees the name
                AIMessage(tool_calls=[ToolCall(name="CustomModel", args={"name": "james"}, id="123")], content="Hi"),
                # the third chunk sees nothing of value
                AIMessage(tool_calls=[ToolCall(name="CustomModel", args={"name": "james"}, id="123")], content="Hi"),
            ]
        )

        with (
            self.extract_structured_data_pipeline(provider, provider_model, pipeline, llm) as graph,
            mock.patch(
                "apps.pipelines.nodes.nodes.ExtractStructuredData.chunk_messages",
                return_value=["I am bond", "james bond", "007"],
            ),
        ):
            state = PipelineState(
                messages=["ai: hi user\nhuman: hi there I am John"],
                experiment_session=session,
            )
            extracted_data = graph.invoke(state)["messages"][-1]

        # This is what the LLM sees.
        inferences = llm.get_call_messages()
        assert inferences[0][0].content == (
            "Extract user data using the current user data and conversation history as reference. Use JSON output."
            "\nCurrent user data:"
            "\n"
            "\nConversation history:"
            "\nI am bond"
            "The conversation history should carry more weight in the outcome. It can change the user's current data"
        )

        assert inferences[1][0].content == (
            "Extract user data using the current user data and conversation history as reference. Use JSON output."
            "\nCurrent user data:"
            "\n{'name': None}"
            "\nConversation history:"
            "\njames bond"
            "The conversation history should carry more weight in the outcome. It can change the user's current data"
        )

        assert inferences[2][0].content == (
            "Extract user data using the current user data and conversation history as reference. Use JSON output."
            "\nCurrent user data:"
            "\n{'name': 'james'}"
            "\nConversation history:"
            "\n007"
            "The conversation history should carry more weight in the outcome. It can change the user's current data"
        )

        # Expected node output
        assert extracted_data == '{"name": "james"}'

    @django_db_with_data()
    def test_extract_participant_data(self, provider, pipeline):
        """Test the pipeline to extract and update participant data. First we run it when no data is linked to the
        participant to make sure it creates data. Then we run it again a few times to test that it updates the data
        correctly.
        """
        session = ExperimentSessionFactory()

        # New data should be created
        data = self._run_data_extract_and_update_pipeline(
            session,
            provider=provider,
            pipeline=pipeline,
            schema='{"name": "the name of the user", "last_name": "the last name of the user"}',
            extracted_data={"name": "Johnny", "last_name": None},
            key_name="profile",
            initial_data={},
        )

        assert data == {"profile": {"name": "Johnny", "last_name": None}}

        # The "profile" key should be updated
        data = self._run_data_extract_and_update_pipeline(
            session,
            provider=provider,
            pipeline=pipeline,
            schema='{"name": "the name of the user", "last_name": "the last name of the user"}',
            extracted_data={"name": "John", "last_name": "Wick"},
            key_name="profile",
            initial_data=data,
        )
        assert data == {"profile": {"name": "John", "last_name": "Wick"}}

        # New data should be inserted at the toplevel
        data = self._run_data_extract_and_update_pipeline(
            session,
            provider=provider,
            pipeline=pipeline,
            schema='{"has_pets": "whether or not the user has pets"}',
            extracted_data={"has_pets": "false"},
            key_name="",
            initial_data=data,
        )
        assert data == {
            "profile": {"name": "John", "last_name": "Wick"},
            "has_pets": "false",
        }

    def _run_data_extract_and_update_pipeline(
        self, session, provider, pipeline, extracted_data: dict, schema: str, key_name: str, initial_data: dict
    ):
        tool_call = AIMessage(tool_calls=[ToolCall(name="CustomModel", args=extracted_data, id="123")], content="Hi")
        service = build_fake_llm_service(responses=[tool_call], token_counts=[0])
        with (
            mock.patch(
                "apps.service_providers.models.LlmProvider.get_llm_service",
                return_value=service,
            ),
        ):
            nodes = [
                start_node(),
                extract_participant_data_node(
                    str(provider.id),
                    str(session.experiment.llm_provider_model.id),
                    schema,
                    key_name,
                ),
                end_node(),
            ]
            runnable = create_runnable(pipeline, nodes)
            state = PipelineState(
                messages=["ai: hi user\nhuman: hi there"],
                experiment_session=session,
                participant_data=initial_data or {},
            )
            result = runnable.invoke(state)
            return result["participant_data"]


class TestAssistantNode:
    """Tests for assistant nodes (OpenAI assistants integration)"""

    def assistant_node_runnable_mock(
        self, output: str, input_message_metadata: dict = None, output_message_metadata: dict = None
    ):
        """A mock for an assistant node runnable that returns the given output and metadata."""
        runnable_mock = Mock()
        runnable_mock.invoke.return_value = ChainOutput(output=output, prompt_tokens=30, completion_tokens=20)
        runnable_mock.history_manager = Mock()
        runnable_mock.history_manager.input_message_metadata = input_message_metadata or {}
        runnable_mock.history_manager.output_message_metadata = output_message_metadata or {}
        return runnable_mock

    @pytest.mark.django_db()
    @pytest.mark.parametrize("tools_enabled", [True, False])
    @patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
    def test_assistant_node(self, get_assistant_runnable, tools_enabled):
        runnable_mock = self.assistant_node_runnable_mock(
            output="Hi there human",
            input_message_metadata={"test": "metadata"},
            output_message_metadata={"test": "metadata"},
        )
        get_assistant_runnable.return_value = runnable_mock

        pipeline = PipelineFactory()
        assistant = OpenAiAssistantFactory(tools=[] if tools_enabled else ["some-tool"])
        nodes = [start_node(), assistant_node(str(assistant.id)), end_node()]
        runnable = create_runnable(pipeline, nodes)
        state = PipelineState(
            messages=["Hi there bot"],
            experiment_session=ExperimentSessionFactory(),
            attachments=[],
        )
        output_state = runnable.invoke(state)
        assert output_state["input_message_metadata"] == {"test": "metadata"}
        assert output_state["output_message_metadata"] == {"test": "metadata"}
        assert output_state["messages"][-1] == "Hi there human"

    @pytest.mark.django_db()
    @patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
    def test_assistant_node_attachments(self, get_assistant_runnable):
        runnable_mock = self.assistant_node_runnable_mock(output="Hi there human")
        get_assistant_runnable.return_value = runnable_mock

        pipeline = PipelineFactory()
        assistant = OpenAiAssistantFactory()
        nodes = [start_node(), assistant_node(str(assistant.id)), end_node()]
        runnable = create_runnable(pipeline, nodes)
        attachments = [
            Attachment(
                file_id=123, type="code_interpreter", name="test.py", size=10, download_link="http://localhost:8000"
            ),
            Attachment(
                file_id=456,
                type="code_interpreter",
                name="demo.py",
                size=10,
                upload_to_assistant=True,
                download_link="http://localhost:8000",
            ),
        ]
        state = PipelineState(
            messages=["Hi there bot"],
            experiment_session=ExperimentSessionFactory(),
            attachments=[att.model_dump() for att in attachments],
        )
        output_state = runnable.invoke(state)
        assert output_state["messages"][-1] == "Hi there human"
        args, kwargs = runnable_mock.invoke.call_args
        assert kwargs["attachments"] == [attachments[1]]

    @django_db_with_data()
    @patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
    def test_assistant_node_raises(self, get_assistant_runnable):
        runnable_mock = runnable_mock = self.assistant_node_runnable_mock(
            output="Hi there human",
            input_message_metadata={"test": "metadata"},
            output_message_metadata={"test": "metadata"},
        )
        get_assistant_runnable.return_value = runnable_mock

        pipeline = PipelineFactory()
        nodes = [start_node(), assistant_node(str(999)), end_node()]
        runnable = create_runnable(pipeline, nodes)
        state = PipelineState(
            messages=["Hi there bot"],
            experiment_session=ExperimentSessionFactory(),
            attachments=[],
        )
        with pytest.raises(PipelineNodeBuildError):
            runnable.invoke(state)

    @pytest.mark.django_db()
    @patch("apps.service_providers.models.LlmProvider.get_llm_service")
    def test_assistant_node_empty_metadata_handling(self, get_llm_service, pipeline):
        history_manager_mock = Mock()
        history_manager_mock.input_message_metadata = None
        history_manager_mock.output_message_metadata = None

        assistant_chat_mock = Mock()
        assistant_chat_mock.history_manager = history_manager_mock
        assistant_chat_mock.invoke = lambda *args, **kwargs: ChainOutput(
            output="How are you doing?", prompt_tokens=30, completion_tokens=20
        )
        assistant = OpenAiAssistantFactory()
        nodes = [start_node(), assistant_node(str(assistant.id)), end_node()]

        with patch("apps.pipelines.nodes.nodes.AssistantChat", return_value=assistant_chat_mock):
            runnable = create_runnable(pipeline, nodes)
            state = PipelineState(
                messages=["I am just a human I have no feelings"],
                experiment_session=ExperimentSessionFactory(),
                attachments=[],
            )
            output_state = runnable.invoke(state)
        assert output_state["input_message_metadata"] == {}
        assert output_state["output_message_metadata"] == {}
        assert output_state["messages"][-1] == "How are you doing?"


class TestPipelineValidation:
    """Tests for pipeline structure validation"""

    @django_db_with_data()
    def test_start_node_missing(self, pipeline):
        nodes = [passthrough_node(), end_node()]
        with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
            create_runnable(pipeline, nodes)

    @django_db_with_data()
    def test_end_node_missing(self, pipeline):
        nodes = [start_node()]
        with pytest.raises(PipelineBuildError, match="There should be exactly 1 End node"):
            create_runnable(pipeline, nodes)

    @django_db_with_data()
    def test_multiple_start_nodes(self, pipeline):
        nodes = [start_node(), start_node(), end_node()]
        with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
            create_runnable(pipeline, nodes)

    @django_db_with_data()
    def test_multiple_end_nodes(self, pipeline):
        nodes = [start_node(), end_node(), end_node()]
        with pytest.raises(PipelineBuildError, match="There should be exactly 1 End node"):
            create_runnable(pipeline, nodes)

    @django_db_with_data()
    def test_single_node_unreachable(self, pipeline):
        # The last passthrough node is not reachable, as it doesn't have any incoming or outgoing edges
        nodes = [start_node(), passthrough_node(), end_node(), passthrough_node()]
        edges = [
            {
                "id": f"{node['id']}->{nodes[i + 1]['id']}",
                "source": node["id"],
                "target": nodes[i + 1]["id"],
            }
            for i, node in enumerate(nodes[:-2])
        ]
        # Should not raise a `ValueError`
        create_runnable(pipeline, nodes, edges)

    @django_db_with_data()
    def test_subgraph_unreachable_should_build(self, pipeline):
        # The last passthrough nodes are not reachable
        start = start_node()
        passthrough = passthrough_node()
        end = end_node()
        nodes = [start, passthrough, end, passthrough_node(), passthrough_node(), passthrough_node()]

        # Start -> Passthrough -> End
        reachable_edges = [
            {
                "id": f"{node['id']}->{nodes[i + 1]['id']}",
                "source": node["id"],
                "target": nodes[i + 1]["id"],
            }
            for i, node in enumerate(nodes[:2])
        ]
        # Passthrough 2 -> Passthrough 3 -> Passthrough 4
        unreachable_edges = [
            {
                "id": f"{node['id']}->{nodes[i + 1]['id']}",
                "source": node["id"],
                "target": nodes[i + 1]["id"],
            }
            for i, node in enumerate(nodes[-3:-1])
        ]
        assert len(unreachable_edges) == 2

        runnable = create_runnable(pipeline, nodes, [*reachable_edges, *unreachable_edges])
        assert set(runnable.get_graph().nodes.keys()) == set(
            ["__start__", start["id"], passthrough["id"], end["id"], "__end__"]
        )

    @django_db_with_data()
    def test_split_graphs_should_not_build(self, pipeline):
        # The last passthrough nodes are not reachable
        start = start_node()
        passthrough_1 = passthrough_node()

        passthrough_2 = passthrough_node()
        end = end_node()
        nodes = [start, passthrough_1, passthrough_2, end]
        edges = [
            {
                "id": "start -> passthrough 1",
                "source": start["id"],
                "target": passthrough_1["id"],
            },
            {
                "id": "passthrough 2 -> end",
                "source": passthrough_2["id"],
                "target": end["id"],
            },
        ]

        with pytest.raises(
            PipelineBuildError,
            match=(
                f"{EndNode.model_config['json_schema_extra'].label} node is not reachable "
                f"from {StartNode.model_config['json_schema_extra'].label} node"
            ),
        ):
            create_runnable(pipeline, nodes, edges)

    @django_db_with_data()
    def test_cyclical_graph(self, pipeline):
        # Ensure that cyclical graphs throw an error
        start = start_node()
        passthrough_1 = passthrough_node()
        passthrough_2 = passthrough_node()
        end = end_node()
        nodes = [start, passthrough_1, passthrough_2, end]
        edges = [
            {
                "id": "start -> passthrough 1",
                "source": start["id"],
                "target": passthrough_1["id"],
            },
            {
                "id": "passthrough 1 -> passthrough 2",
                "source": passthrough_1["id"],
                "target": passthrough_2["id"],
            },
            {
                "id": "passthrough 2 -> passthrough 1",
                "source": passthrough_2["id"],
                "target": passthrough_1["id"],
            },
            {
                "id": "passthrough 2 -> end",
                "source": passthrough_2["id"],
                "target": end["id"],
            },
        ]

        with pytest.raises(PipelineBuildError, match="A cycle was detected"):
            create_runnable(pipeline, nodes, edges)

    @django_db_with_data()
    def test_multiple_valid_inputs(self, pipeline):
        """This tests the case where a node has multiple valid inputs to make sure it selects the correct one.

        start --> router -+-> template --> end
                          |                 ^
                          +---------- ------+

        In this graph, the end node can have valid input from 'router' and 'template' (if the router routes
        to the template node). The end node should select the input from the 'template' and not the 'router'.
        """
        start = start_node()
        router = boolean_node()
        template = render_template_node("T: {{ input }}")
        end = end_node()
        nodes = [start, router, template, end]

        edges = [
            {
                "id": "start -> router",
                "source": start["id"],
                "target": router["id"],
            },
            {
                "id": "router -> template",
                "source": router["id"],
                "target": template["id"],
                "sourceHandle": "output_1",
            },
            {
                "id": "template -> end",
                "source": template["id"],
                "target": end["id"],
            },
            {
                "id": "router -> end",
                "source": router["id"],
                "target": end["id"],
                "sourceHandle": "output_0",
            },
        ]
        experiment_session = ExperimentSessionFactory.create()
        state = PipelineState(
            messages=["not hello"],
            experiment_session=experiment_session,
        )
        output = create_runnable(pipeline, nodes, edges).invoke(state)
        assert output["messages"][-1] == "T: not hello"


class TestPipelineStateHelpers:
    """Tests for PipelineState helper methods and utilities"""

    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ({}, {"key": [1]}, {"key": [1]}),
            ({"key": [1]}, {"key": [2]}, {"key": [1, 2]}),
            ({"key": [1]}, {"key": [1]}, {"key": [1]}),
            ({"keyA": [1]}, {"keyB": [2]}, {"keyA": [1], "keyB": [2]}),
            ({"keyA": True}, {"keyA": False}, {"keyA": False}),
        ],
    )
    def test_merge_dicts(self, left, right, expected):
        assert merge_dict_values_as_lists(left, right) == expected

    def test_input_with_format_strings(self):
        state = PipelineState(
            messages=["Is this it {the thing}"],
            experiment_session=ExperimentSessionFactory.build(),
            temp_state={},
        )
        resp = Passthrough(node_id="test", django_node=None, name="test").process([], [], state, {})

        assert resp["messages"] == ["Is this it {the thing}"]

    def test_get_selected_route(self):
        pipeline_state_json = {
            "outputs": {
                "router_1": {"message": "hello", "node_id": "node1", "route": "path_a"},
                "router_2": {"message": "world", "node_id": "node2", "route": "path_b"},
                "normal_node": {"message": "test", "node_id": "node3"},
            },
            "messages": ["hello world"],
            "temp_state": {"user_input": "hello world", "outputs": {}},
            "path": [],
        }

        state = PipelineState(**pipeline_state_json)

        assert state.get_selected_route("router_1") == "path_a"
        assert state.get_selected_route("router_2") == "path_b"
        assert state.get_selected_route("normal_node") is None
        assert state.get_selected_route("non_existent_node") is None

    def test_get_all_routes(self):
        pipeline_state_json = {
            "outputs": {
                "router_1": {"message": "hello", "node_id": "node1", "route": "path_a"},
                "router_2": {"message": "world", "node_id": "node2", "route": "path_b"},
                "router_3": {"message": "test", "node_id": "node3", "route": "path_c"},
                "normal_node": {"message": "test", "node_id": "node4"},
            },
            "messages": ["hello world"],
            "temp_state": {"user_input": "hello world", "outputs": {}},
            "path": [],
        }
        state = PipelineState(**pipeline_state_json)
        expected_routes = {"router_1": "path_a", "router_2": "path_b", "router_3": "path_c"}
        assert state.get_all_routes() == expected_routes

        # no router node case
        pipeline_state_json = {
            "outputs": {"normal_node": {"message": "test", "node_id": "node4"}},
            "messages": ["hello world"],
            "temp_state": {"user_input": "hello world", "outputs": {}},
            "path": [],
        }
        state = PipelineState(**pipeline_state_json)
        assert state.get_all_routes() == {}

    def test_get_node_path(self):
        pipeline_state_json = {
            "outputs": {
                "start": {"message": "start", "node_id": "id_start"},
                "router": {"message": "route", "node_id": "id_router", "route": "branch_a"},
                "branch_a": {"message": "a", "node_id": "id_branch_a"},
                "branch_b": {"message": "b", "node_id": "id_branch_b"},
                "end": {"message": "end", "node_id": "id_end"},
            },
            "messages": ["test message"],
            "temp_state": {"user_input": "test message", "outputs": {}},
            "path": [
                (None, "id_start", ["id_router"]),
                ("id_start", "id_router", ["id_branch_a", "id_branch_b"]),
                ("id_router", "id_branch_a", ["id_end"]),
                ("id_branch_a", "id_end", []),
            ],
        }
        state = PipelineState(**pipeline_state_json)

        assert state.get_node_path("start") == ["start"]
        assert state.get_node_path("branch_a") == ["start", "router", "branch_a"]
        assert state.get_node_path("branch_b") == ["start", "router", "branch_b"]
        assert state.get_node_path("end") == ["start", "router", "branch_a", "end"]
        assert state.get_node_path("nonexistent_node") == ["nonexistent_node"]
