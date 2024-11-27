from contextlib import contextmanager
from unittest import mock
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core import mail
from django.test import override_settings
from langgraph.graph.state import CompiledStateGraph

from apps.experiments.models import ParticipantData
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.flow import FlowNode
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes import nodes
from apps.pipelines.nodes.base import PipelineState
from apps.service_providers.llm_service.runnables import ChainOutput
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.langchain import (
    FakeLlmSimpleTokenCount,
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


def _make_edges(nodes) -> list[dict]:
    if len(nodes) <= 1:
        return []

    return [
        {
            "id": f"{node['id']}->{nodes[i+1]['id']}",
            "source": node["id"],
            "target": nodes[i + 1]["id"],
        }
        for i, node in enumerate(nodes[:-1])
    ]


def _create_runnable(pipeline: Pipeline, nodes: list[dict], edges: list[dict] | None = None) -> CompiledStateGraph:
    if edges is None:
        edges = _make_edges(nodes)
    flow_nodes = []
    for node in nodes:
        flow_nodes.append({"id": node["id"], "data": node})
    pipeline.data = {"edges": edges, "nodes": flow_nodes}
    pipeline.set_nodes([FlowNode(**flow_node) for flow_node in flow_nodes])
    return PipelineGraph.build_runnable_from_pipeline(pipeline)


def _create_start_node():
    return {"id": str(uuid4()), "type": "StartNode"}


def _create_email_node():
    return {
        "id": str(uuid4()),
        "label": "Send an email",
        "type": "SendEmail",
        "params": {
            "recipient_list": "test@example.com",
            "subject": "This is an interesting email",
        },
    }


def _create_llm_response_with_prompt_node(
    provider_id: str, provider_model_id: str, source_material_id: str | None = None, prompt: str | None = None
):
    if prompt is None:
        prompt = (
            "Make a summary of the following text: {input}. "
            "Output it as JSON with a single key called 'summary' with the summary."
        )
    params = {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": provider_model_id,
        "prompt": prompt,
    }
    if source_material_id is not None:
        params["source_material_id"] = source_material_id
    return {
        "id": str(uuid4()),
        "type": "LLMResponseWithPrompt",
        "params": params,
    }


def _create_llm_response_node(provider_id: str, provider_model_id: str):
    return {
        "id": str(uuid4()),
        "type": nodes.LLMResponse.__name__,
        "params": {
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
        },
    }


def _create_render_template_node(template_string: str | None = None):
    if template_string is None:
        template_string = "<b>{{ summary }}</b>"
    return {
        "id": str(uuid4()),
        "type": nodes.RenderTemplate.__name__,
        "params": {
            "template_string": template_string,
        },
    }


def _create_passthrough_node():
    return {
        "id": str(uuid4()),
        "type": nodes.Passthrough.__name__,
    }


def _create_boolean_node():
    return {
        "id": str(uuid4()),
        "type": nodes.BooleanNode.__name__,
        "params": {"input_equals": "hello"},
    }


def _create_router_node(provider_id: str, provider_model_id: str, keywords: list[str]):
    return {
        "id": str(uuid4()),
        "type": nodes.RouterNode.__name__,
        "params": {
            "prompt": "You are a router",
            "keywords": keywords,
            "num_outputs": len(keywords),
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
        },
    }


def _create_assistant_node(assistant_id: str):
    return {
        "id": str(uuid4()),
        "type": nodes.AssistantNode.__name__,
        "params": {
            "assistant_id": assistant_id,
            "citations_enabled": True,
            "input_formatter": "",
        },
    }


def _create_extract_participant_data_node(provider_id: str, provider_model_id: str, data_schema: str, key_name: str):
    return {
        "id": str(uuid4()),
        "type": nodes.ExtractParticipantData.__name__,
        "params": {
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
            "key_name": key_name,
        },
    }


def _create_extract_structured_data_node(provider_id: str, provider_model_id: str, data_schema: str):
    return {
        "id": str(uuid4()),
        "type": nodes.ExtractStructuredData.__name__,
        "params": {
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
        },
    }


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_full_email_sending_pipeline(get_llm_service, provider, provider_model, pipeline):
    service = build_fake_llm_service(responses=['{"summary": "Ice is cold"}'], token_counts=[0])
    get_llm_service.return_value = service

    nodes = [
        _create_start_node(),
        _create_render_template_node(),
        _create_llm_response_with_prompt_node(str(provider.id), str(provider_model.id)),
        _create_email_node(),
    ]

    state = PipelineState(
        messages=["Ice is not a liquid. When it is melted it turns into water."],
        experiment_session=None,
    )
    _create_runnable(pipeline, nodes).invoke(state)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_send_email(pipeline):
    nodes = [_create_start_node(), _create_email_node()]
    _create_runnable(pipeline, nodes).invoke(PipelineState(messages=["A cool message"]))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_response(get_llm_service, provider, provider_model, pipeline):
    service = build_fake_llm_service(responses=["123"], token_counts=[0])
    get_llm_service.return_value = service
    nodes = [_create_start_node(), _create_llm_response_node(str(provider.id), str(provider_model.id))]
    assert (
        _create_runnable(pipeline, nodes).invoke(PipelineState(messages=["Repeat exactly: 123"]))["messages"][-1]
        == "123"
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_with_prompt_response(
    get_llm_service, provider, provider_model, pipeline, source_material, experiment_session
):
    service = build_fake_llm_echo_service()
    get_llm_service.return_value = service

    user_input = "The User Input"
    participant_data = ParticipantData.objects.create(
        team=experiment_session.team,
        content_object=experiment_session.experiment,
        participant=experiment_session.participant,
        data={"name": "A"},
    )
    nodes = [
        _create_start_node(),
        _create_llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            source_material_id=str(source_material.id),
            prompt="Node 1: Use this {source_material} to answer questions about {participant_data}.",
        ),
        _create_llm_response_with_prompt_node(
            str(provider.id), str(provider_model.id), source_material_id=str(source_material.id), prompt="Node 2:"
        ),
    ]
    output = _create_runnable(pipeline, nodes).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["messages"][-1]
    expected_output = (
        f"Node 2: Node 1: Use this {source_material.material} to answer questions "
        f"about {participant_data.data}. {user_input}"
    )
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_render_template(pipeline):
    nodes = [_create_start_node(), _create_render_template_node("{{ thing }} is cool")]
    assert (
        _create_runnable(pipeline, nodes).invoke(PipelineState(messages=[{"thing": "Cycling"}]))["messages"][-1]
        == "Cycling is cool"
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_branching_pipeline(pipeline, experiment_session):
    start = _create_start_node()
    A = _create_render_template_node("A ({{input }})")
    B = _create_render_template_node("B ({{ input}})")
    C = _create_render_template_node("C ({{input }})")
    END = _create_passthrough_node()  # TODO: Convert to endnode
    nodes = [
        start,
        A,
        B,
        C,
        END,
    ]
    edges = [
        {
            "id": "start -> RenderTemplate-A",
            "source": start["id"],
            "target": A["id"],
        },
        {
            "id": "start -> RenderTemplate-B",
            "source": start["id"],
            "target": B["id"],
        },
        {
            "id": "RenderTemplate-A -> END",
            "source": A["id"],
            "target": END["id"],
        },
        {
            "id": "RenderTemplate-B -> RenderTemplate-C",
            "source": B["id"],
            "target": C["id"],
        },
        {
            "id": "RenderTemplate-C -> END",
            "source": C["id"],
            "target": END["id"],
        },
    ]
    user_input = "The Input"
    output = _create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["outputs"]
    expected_output = {
        start["id"]: {"message": user_input},
        A["id"]: {"message": f"A ({user_input})"},
        B["id"]: {"message": f"B ({user_input})"},
        C["id"]: {"message": f"C (B ({user_input}))"},
        END["id"]: [{"message": f"A ({user_input})"}, {"message": f"C (B ({user_input}))"}],
    }
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_conditional_node(pipeline, experiment_session):
    start = _create_start_node()
    boolean = _create_boolean_node()
    template_true = _create_render_template_node("said hello")
    template_false = _create_render_template_node("didn't say hello, said {{ input }}")
    end = _create_passthrough_node()
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
    runnable = _create_runnable(pipeline, nodes, edges)
    output = runnable.invoke(PipelineState(messages=["hello"], experiment_session=experiment_session))
    assert output["messages"][-1] == "said hello"
    assert output["outputs"] == {
        start["id"]: {"message": "hello"},
        boolean["id"]: {"message": "hello", "output_handle": "output_0"},
        template_true["id"]: {"message": "said hello"},
        end["id"]: {"message": "said hello"},
    }

    output = runnable.invoke(PipelineState(messages=["bad"], experiment_session=experiment_session))
    assert output["messages"][-1] == "didn't say hello, said bad"
    assert output["outputs"] == {
        start["id"]: {"message": "bad"},
        boolean["id"]: {"message": "bad", "output_handle": "output_1"},
        template_false["id"]: {"message": "didn't say hello, said bad"},
        end["id"]: {"message": "didn't say hello, said bad"},
    }


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_router_node(get_llm_service, provider, provider_model, pipeline, experiment_session):
    service = build_fake_llm_echo_service(include_system_message=False)
    get_llm_service.return_value = service
    start = _create_start_node()
    router = _create_router_node(str(provider.id), str(provider_model.id), keywords=["A", "b", "c", "d"])
    template_a = _create_render_template_node("A {{ input }}")
    template_b = _create_render_template_node("B {{ input }}")
    template_c = _create_render_template_node("C {{ input }}")
    template_d = _create_render_template_node("D {{ input }}")
    end = _create_passthrough_node()
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
    runnable = _create_runnable(pipeline, nodes, edges)

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


@contextmanager
def extract_structured_data_pipeline(provider, provider_model, pipeline, llm=None):
    service = build_fake_llm_service(responses=[{"name": "John"}], token_counts=[0], fake_llm=llm)

    with (
        mock.patch(
            "apps.service_providers.models.LlmProvider.get_llm_service",
            return_value=service,
        ),
    ):
        nodes = [
            _create_start_node(),
            _create_extract_structured_data_node(
                str(provider.id), str(provider_model.id), '{"name": "the name of the user"}'
            ),
        ]
        runnable = _create_runnable(pipeline, nodes)
        yield runnable


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_no_chunking(provider, provider_model, pipeline):
    session = ExperimentSessionFactory()

    with extract_structured_data_pipeline(provider, provider_model, pipeline) as graph:
        state = PipelineState(
            messages=["ai: hi user\nhuman: hi there I am John"],
            experiment_session=session,
        )
        assert graph.invoke(state)["messages"][-1] == '{"name": "John"}'


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_with_chunking(provider, provider_model, pipeline):
    session = ExperimentSessionFactory()
    ParticipantData.objects.create(
        team=session.team,
        content_object=session.experiment,
        data={"drink": "martini"},
        participant=session.participant,
    )
    llm = FakeLlmSimpleTokenCount(
        responses=[
            {"name": None},  # the first chunk sees nothing of value
            {"name": "james"},  # the second chunk message sees the name
            {"name": "james"},  # the third chunk sees nothing of value
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
    assert inferences[0][0].text == (
        "Extract user data using the current user data and conversation history as reference. Use JSON output."
        "\nCurrent user data:"
        "\n"
        "\nConversation history:"
        "\nI am bond"
        "The conversation history should carry more weight in the outcome. It can change the user's current data"
    )

    assert inferences[1][0].text == (
        "Extract user data using the current user data and conversation history as reference. Use JSON output."
        "\nCurrent user data:"
        "\n{'name': None}"
        "\nConversation history:"
        "\njames bond"
        "The conversation history should carry more weight in the outcome. It can change the user's current data"
    )

    assert inferences[2][0].text == (
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
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
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
        extracted_data={"name": "Johnny"},
        key_name="profile",
    )

    participant_data = ParticipantData.objects.for_experiment(session.experiment).get(participant=session.participant)
    assert participant_data.data == {"profile": {"name": "Johnny"}}

    # The "profile" key should be updated
    _run_data_extract_and_update_pipeline(
        session,
        provider=provider,
        pipeline=pipeline,
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
        extracted_data={"has_pets": False},
        key_name="",
    )
    participant_data.refresh_from_db()
    assert participant_data.data == {
        "profile": {"name": "John", "last_name": "Wick"},
        "has_pets": False,
    }


def _run_data_extract_and_update_pipeline(session, provider, pipeline, extracted_data: dict, key_name: str):
    service = build_fake_llm_service(responses=[extracted_data], token_counts=[0])

    with (
        mock.patch(
            "apps.service_providers.models.LlmProvider.get_llm_service",
            return_value=service,
        ),
    ):
        nodes = [
            _create_start_node(),
            _create_extract_participant_data_node(
                str(provider.id),
                str(session.experiment.llm_provider_model.id),
                '{"name": "the name of the user"}',
                key_name,
            ),
        ]
        runnable = _create_runnable(pipeline, nodes)
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session=session)
        runnable.invoke(state)


@pytest.mark.django_db()
@pytest.mark.parametrize("tools_enabled", [True, False])
@patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
def test_assistant_node(get_assistant_runnable, tools_enabled):
    runnable_mock = Mock()
    runnable_mock.invoke = lambda *args, **kwargs: ChainOutput(
        output="Hi there human", prompt_tokens=30, completion_tokens=20
    )
    runnable_mock.state.get_message_metadata = lambda *args, **kwargs: {"test": "metadata"}
    get_assistant_runnable.return_value = runnable_mock

    pipeline = PipelineFactory()
    assistant = OpenAiAssistantFactory(tools=[] if tools_enabled else ["some-tool"])
    nodes = [_create_start_node(), _create_assistant_node(str(assistant.id))]
    runnable = _create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Hi there bot"],
        experiment_session=ExperimentSessionFactory(),
        attachments=[],
    )
    output_state = runnable.invoke(state)
    assert output_state["message_metadata"]["input"] == {"test": "metadata"}
    assert output_state["message_metadata"]["output"] == {"test": "metadata"}
    assert output_state["messages"][-1] == "Hi there human"


@pytest.mark.django_db()
@patch("apps.pipelines.nodes.nodes.AssistantNode._get_assistant_runnable")
def test_assistant_node_raises(get_assistant_runnable):
    runnable_mock = Mock()
    runnable_mock.invoke = lambda *args, **kwargs: ChainOutput(
        output="Hi there human", prompt_tokens=30, completion_tokens=20
    )
    runnable_mock.state.get_message_metadata = lambda *args, **kwargs: {"test": "metadata"}
    get_assistant_runnable.return_value = runnable_mock

    pipeline = PipelineFactory()
    nodes = [_create_start_node(), _create_assistant_node(str(999))]
    runnable = _create_runnable(pipeline, nodes)
    state = PipelineState(
        messages=["Hi there bot"],
        experiment_session=ExperimentSessionFactory(),
        attachments=[],
    )
    with pytest.raises(PipelineNodeBuildError):
        runnable.invoke(state)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_start_node_missing(pipeline):
    nodes = [_create_passthrough_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
        _create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_multiple_start_nodes(pipeline):
    nodes = [_create_start_node(), _create_start_node()]
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 Start node"):
        _create_runnable(pipeline, nodes)


@django_db_with_data(available_apps=("apps.service_providers",))
@pytest.mark.skip()
def test_end_node_missing(pipeline):
    data = {
        "edges": [],
        "nodes": [
            {
                "data": {
                    "id": "start-GUk0C",
                    "label": "Start",
                    "type": "StartNode",
                },
                "id": "start-GUk0C",
            }
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    with pytest.raises(PipelineBuildError, match="There should be exactly 1 End node"):
        runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    assert runnable.invoke(PipelineState(messages=["Repeat exactly: 123"]))["messages"][-1] == "Repeat exactly: 123"
