from contextlib import contextmanager
from unittest import mock
from unittest.mock import Mock, patch

import pytest
from django.core import mail
from django.test import override_settings
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCall, ToolCallChunk
from langchain_openai.chat_models.base import OpenAIRefusalError

from apps.annotations.models import TagCategories
from apps.channels.datamodels import Attachment
from apps.experiments.models import AgentTools, ParticipantData
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.nodes.base import Intents, PipelineState, merge_dicts
from apps.pipelines.nodes.nodes import EndNode, Passthrough, RouterNode, StartNode, StaticRouterNode
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
from apps.service_providers.llm_service.history_managers import PipelineHistoryManager
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy
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


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_full_email_sending_pipeline(get_llm_service, provider, provider_model, pipeline):
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
@django_db_with_data(available_apps=("apps.service_providers",))
def test_send_email(pipeline):
    nodes = [start_node(), email_node(), end_node()]
    create_runnable(pipeline, nodes).invoke(PipelineState(messages=["A cool message"]))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_response(get_llm_service, provider, provider_model, pipeline):
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


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_with_prompt_response(
    get_llm_service, provider, provider_model, pipeline, source_material, experiment_session
):
    service = build_fake_llm_echo_service()
    get_llm_service.return_value = service

    user_input = "The User Input"
    participant_data = ParticipantData.objects.create(
        team=experiment_session.team,
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        data={"name": "A"},
    )
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
    experiment_session.state = {"session_key": "session_value"}
    output = create_runnable(pipeline, nodes).invoke(
        PipelineState(
            messages=[user_input], experiment_session=experiment_session, temp_state={"temp_key": "temp_value"}
        )
    )["messages"][-1]
    expected_output = (
        f"Node 2: temp_value session_value Node 1: Use this {source_material.material} to answer questions "
        f"about {participant_data.data}. {user_input}"
    )
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
def test_render_template(pipeline):
    nodes = [
        start_node(),
        render_template_node("{{ input }} is cool"),
        end_node(),
    ]

    result = create_runnable(pipeline, nodes).invoke(PipelineState(messages=["Cycling"]))
    assert result["messages"][-1] == "Cycling is cool"


@django_db_with_data(available_apps=("apps.service_providers",))
def test_conditional_node(pipeline, experiment_session):
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


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_router_node_prompt(get_llm_service, provider, provider_model, pipeline, experiment_session):
    service = build_fake_llm_echo_service()
    get_llm_service.return_value = service

    node = RouterNode(
        node_id="test",
        django_node=None,
        name="test router",
        prompt="PD: {participant_data}",
        keywords=["A"],
        llm_provider_id=provider.id,
        llm_provider_model_id=provider_model.id,
    )
    node._process_conditional(
        PipelineState(
            outputs={"123": {"message": "a"}}, messages=["a"], experiment_session=experiment_session, node_input="a"
        ),
    )

    assert len(service.llm.get_call_messages()[0]) == 2
    proxy = ParticipantDataProxy(experiment_session)
    assert str(proxy.get()) in service.llm.get_call_messages()[0][0].content


@django_db_with_data(available_apps=("apps.service_providers",))
def test_static_router_temp_state(pipeline, experiment_session):
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
    router = state_key_router_node("route_to", ["first", "second"], data_source=StaticRouterNode.DataSource.temp_state)
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_static_router_case_sensitive(pipeline, experiment_session):
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
def test_router_sets_tags_correctly(pipeline, experiment_session):
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


@django_db_with_data(available_apps=("apps.service_providers",))
@pytest.mark.parametrize(
    "data_source", [StaticRouterNode.DataSource.participant_data, StaticRouterNode.DataSource.session_state]
)
def test_static_router_participant_data(data_source, pipeline, experiment_session):
    def _update_participant_data(session, data):
        ParticipantDataProxy(session).set(data)

    def _update_session_state(session, data):
        session.state = data
        session.save(update_fields=["state"])

    DATA_SOURCE_UPDATERS = {
        StaticRouterNode.DataSource.participant_data: _update_participant_data,
        StaticRouterNode.DataSource.session_state: _update_session_state,
    }

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

    DATA_SOURCE_UPDATERS[data_source](experiment_session, {"route_to": "first"})
    output = runnable.invoke(PipelineState(messages=["Hi"], experiment_session=experiment_session))
    assert output["messages"][-1] == "A Hi"

    DATA_SOURCE_UPDATERS[data_source](experiment_session, {"route_to": "second"})
    output = runnable.invoke(PipelineState(messages=["Hi"], experiment_session=experiment_session))
    assert output["messages"][-1] == "B Hi"

    # default route
    DATA_SOURCE_UPDATERS[data_source](experiment_session, {})
    output = runnable.invoke(PipelineState(messages=["Hi"], experiment_session=experiment_session))
    assert output["messages"][-1] == "A Hi"


@django_db_with_data(available_apps=("apps.service_providers",))
def test_attachments_in_code_node(pipeline, experiment_session):
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


@contextmanager
def extract_structured_data_pipeline(provider, provider_model, pipeline, llm=None):
    tool_response = AIMessage(tool_calls=[ToolCall(name="CustomModel", args={"name": "John"}, id="123")], content="Hi")
    service = build_fake_llm_service(responses=[tool_response], token_counts=[0], fake_llm=llm)

    with (
        mock.patch(
            "apps.service_providers.models.LlmProvider.get_llm_service",
            return_value=service,
        ),
    ):
        nodes = [
            start_node(),
            extract_structured_data_node(str(provider.id), str(provider_model.id), '{"name": "the name of the user"}'),
            end_node(),
        ]
        runnable = create_runnable(pipeline, nodes)
        yield runnable


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
def test_extract_structured_data_no_chunking(provider, provider_model, pipeline):
    session = ExperimentSessionFactory()

    with extract_structured_data_pipeline(provider, provider_model, pipeline) as graph:
        state = PipelineState(
            messages=["ai: hi user\nhuman: hi there I am John"],
            experiment_session=session,
        )
        assert graph.invoke(state)["messages"][-1] == '{"name": "John"}'


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
def test_extract_structured_data_with_chunking(provider, provider_model, pipeline):
    session = ExperimentSessionFactory()
    ParticipantData.objects.create(
        team=session.team,
        experiment=session.experiment,
        data={"drink": "martini"},
        participant=session.participant,
    )
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
        extract_structured_data_pipeline(provider, provider_model, pipeline, llm) as graph,
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


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
def test_extract_participant_data(provider, pipeline):
    """Test the pipeline to extract and update participant data. First we run it when no data is linked to the
    participant to make sure it creates data. Then we run it again a few times to test that it updates the data
    correctly.
    """
    session = ExperimentSessionFactory()
    session.participant.team = session.team
    session.participant.save()
    # There should be no data
    participant_data = (
        ParticipantData.objects.for_experiment(session.experiment).filter(participant=session.participant).first()
    )
    assert participant_data is None

    # New data should be created
    _run_data_extract_and_update_pipeline(
        session,
        provider=provider,
        pipeline=pipeline,
        schema='{"name": "the name of the user", "last_name": "the last name of the user"}',
        extracted_data={"name": "Johnny", "last_name": None},
        key_name="profile",
    )

    participant_data = ParticipantData.objects.for_experiment(session.experiment).get(participant=session.participant)
    assert participant_data.data == {"profile": {"name": "Johnny", "last_name": None}}

    # The "profile" key should be updated
    _run_data_extract_and_update_pipeline(
        session,
        provider=provider,
        pipeline=pipeline,
        schema='{"name": "the name of the user", "last_name": "the last name of the user"}',
        extracted_data={"name": "John", "last_name": "Wick"},
        key_name="profile",
    )
    participant_data.refresh_from_db()
    assert participant_data.data == {"profile": {"name": "John", "last_name": "Wick"}}

    # New data should be inserted at the toplevel
    _run_data_extract_and_update_pipeline(
        session,
        provider=provider,
        pipeline=pipeline,
        schema='{"has_pets": "whether or not the user has pets"}',
        extracted_data={"has_pets": "false"},
        key_name="",
    )
    participant_data.refresh_from_db()
    assert participant_data.data == {
        "profile": {"name": "John", "last_name": "Wick"},
        "has_pets": "false",
    }


def _run_data_extract_and_update_pipeline(
    session, provider, pipeline, extracted_data: dict, schema: dict, key_name: str
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
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session=session)
        runnable.invoke(state)


def assistant_node_runnable_mock(
    output: str, input_message_metadata: dict = None, output_message_metadata: dict = None
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
def test_assistant_node(get_assistant_runnable, tools_enabled):
    runnable_mock = assistant_node_runnable_mock(
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
def test_assistant_node_attachments(get_assistant_runnable):
    runnable_mock = assistant_node_runnable_mock(output="Hi there human")
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


@django_db_with_data(available_apps=("apps.service_providers",))
@patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
def test_assistant_node_raises(get_assistant_runnable):
    runnable_mock = runnable_mock = assistant_node_runnable_mock(
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_start_node_missing(pipeline):
    nodes = [passthrough_node(), end_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
        create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_end_node_missing(pipeline):
    nodes = [start_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 End node"):
        create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_multiple_start_nodes(pipeline):
    nodes = [start_node(), start_node(), end_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
        create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_multiple_end_nodes(pipeline):
    nodes = [start_node(), end_node(), end_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 End node"):
        create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_single_node_unreachable(pipeline):
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_subgraph_unreachable_should_build(pipeline):
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_split_graphs_should_not_build(pipeline):
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_cyclical_graph(pipeline):
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_multiple_valid_inputs(pipeline):
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


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_assistant_node_empty_metadata_handling(get_llm_service, pipeline):
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


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_pipeline_history_manager_metadata_storage(get_llm_service, pipeline):
    history_manager = PipelineHistoryManager.for_assistant()
    input_metadata = {"test": "metatdata", "timestamp": "2025-03-06"}
    output_metadata = {"test": "metadata", "tokens": 150}

    history_manager.add_messages_to_history(
        input="Hi Bot",
        input_message_metadata=input_metadata,
        output="Hi Human",
        output_message_metadata=output_metadata,
        save_input_to_history=True,
        save_output_to_history=True,
        experiment_tag=None,
    )
    assert history_manager.input_message_metadata == input_metadata
    assert history_manager.output_message_metadata == output_metadata


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
def test_merge_dicts(left, right, expected):
    assert merge_dicts(left, right) == expected


def test_input_with_format_strings():
    state = PipelineState(
        messages=["Is this it {the thing}"],
        experiment_session=ExperimentSessionFactory.build(),
        temp_state={},
    )
    resp = Passthrough(node_id="test", django_node=None, name="test").process([], [], state, {})

    assert resp["messages"] == ["Is this it {the thing}"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_router_node(get_llm_service, provider, provider_model, pipeline, experiment_session):
    def _tool_call(route):
        return AIMessage(tool_calls=[ToolCall(name="RouterOutput", args={"route": route}, id="123")], content=route)

    service = build_fake_llm_service(
        responses=[
            _tool_call("a"),
            _tool_call("A"),
            _tool_call("b"),
            _tool_call("c"),
            _tool_call("d"),
            _tool_call("z"),
        ],
        token_counts=[0],
    )
    get_llm_service.return_value = service
    start = start_node()
    router = router_node(str(provider.id), str(provider_model.id), keywords=["A", "b", "c", "d"])
    template_a = render_template_node("A {{ input }}")
    template_b = render_template_node("B {{ input }}")
    template_c = render_template_node("C {{ input }}")
    template_d = render_template_node("D {{ input }}")
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
    assert output["messages"][-1] == "A a"
    output = runnable.invoke(PipelineState(messages=["A"], experiment_session=experiment_session))
    assert output["messages"][-1] == "A A"
    output = runnable.invoke(PipelineState(messages=["b"], experiment_session=experiment_session))
    assert output["messages"][-1] == "B b"
    output = runnable.invoke(PipelineState(messages=["c"], experiment_session=experiment_session))
    assert output["messages"][-1] == "C c"
    output = runnable.invoke(PipelineState(messages=["d"], experiment_session=experiment_session))
    assert output["messages"][-1] == "D d"
    output = runnable.invoke(PipelineState(messages=["z"], experiment_session=experiment_session))
    assert output["messages"][-1] == "A z"


@pytest.mark.django_db()
def test_router_node_output_structure(provider, provider_model, pipeline, experiment_session):
    service = build_fake_llm_echo_service()
    with mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service):
        node_id = "123"
        node = RouterNode(
            node_id=node_id,
            django_node=None,
            name="test_router",
            prompt="PD: {participant_data}",
            keywords=["A"],
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
        with mock.patch.object(node, "_process_conditional", return_value=("A", True)):
            edge_map = {"A": "next_node_a", "B": "next_node_b"}
            incoming_edges = ["prev_node"]
            router_func = node.build_router_function(edge_map, incoming_edges)
            command = router_func(state, {})

            output_state = command.update

            assert node.name in output_state["outputs"]
            assert "route" in output_state["outputs"][node.name]
            assert "message" in output_state["outputs"][node.name]
            assert output_state["outputs"][node.name]["route"] == "A"
            assert output_state["outputs"][node.name]["message"] == "hello world"
            assert command.goto == ["next_node_a"]


def test_get_selected_route():
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


def test_get_all_routes():
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


def test_get_node_path():
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


class RefusingFakeLlmEcho(FakeLlmEcho):
    def invoke(self, *args, **kwargs):
        raise OpenAIRefusalError("Refused by OpenAI")


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_router_node_openai_refusal_uses_default_keyword(get_llm_service, provider, provider_model, experiment_session):
    refusing_llm = RefusingFakeLlmEcho(include_system_message=True)
    service = FakeLlmService(llm=refusing_llm, token_counter=FakeTokenCounter(token_counts=[0]))
    get_llm_service.return_value = service
    node = RouterNode(
        node_id="test",
        django_node=None,
        name="test router",
        prompt="PD: {participant_data}",
        keywords=["DEFAULT", "A", "B"],
        llm_provider_id=provider.id,
        llm_provider_model_id=provider_model.id,
    )
    node.default_keyword_index = 0
    state = PipelineState(
        outputs={"123": {"message": "a"}}, messages=["a"], experiment_session=experiment_session, node_input="a"
    )

    keyword, is_default_keyword = node._process_conditional(state)
    assert keyword == "DEFAULT"
    assert is_default_keyword


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_end_session_tool(get_llm_service, provider, provider_model, pipeline, experiment_session):
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
    print(output)
    assert output["intents"] == [Intents.END_SESSION]
