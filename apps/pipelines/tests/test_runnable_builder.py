from contextlib import contextmanager
from unittest import mock
from unittest.mock import Mock, patch

import pytest
from django.core import mail
from django.test import override_settings

from apps.experiments.models import ParticipantData
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import EndNode, StartNode
from apps.pipelines.tests.utils import (
    assistant_node,
    boolean_node,
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


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
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
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_send_email(pipeline):
    nodes = [start_node(), email_node(), end_node()]
    create_runnable(pipeline, nodes).invoke(PipelineState(messages=["A cool message"]))
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
        start_node(),
        llm_response_with_prompt_node(
            str(provider.id),
            str(provider_model.id),
            source_material_id=str(source_material.id),
            prompt="Node 1: Use this {source_material} to answer questions about {participant_data}.",
        ),
        llm_response_with_prompt_node(
            str(provider.id), str(provider_model.id), source_material_id=str(source_material.id), prompt="Node 2:"
        ),
        end_node(),
    ]
    output = create_runnable(pipeline, nodes).invoke(
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
    nodes = [
        start_node(),
        render_template_node("{{ thing }} is cool"),
        end_node(),
    ]
    assert (
        create_runnable(pipeline, nodes).invoke(PipelineState(messages=[{"thing": "Cycling"}]))["messages"][-1]
        == "Cycling is cool"
    )


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_branching_pipeline(pipeline, experiment_session):
    start = start_node()
    template_a = render_template_node("A ({{input }})")
    template_b = render_template_node("B ({{ input}})")
    template_c = render_template_node("C ({{input }})")
    end = end_node()
    nodes = [
        start,
        template_a,
        template_b,
        template_c,
        end,
    ]
    edges = [
        {
            "id": "start -> RenderTemplate-A",
            "source": start["id"],
            "target": template_a["id"],
        },
        {
            "id": "start -> RenderTemplate-B",
            "source": start["id"],
            "target": template_b["id"],
        },
        {
            "id": "RenderTemplate-A -> END",
            "source": template_a["id"],
            "target": end["id"],
        },
        {
            "id": "RenderTemplate-B -> RenderTemplate-C",
            "source": template_b["id"],
            "target": template_c["id"],
        },
        {
            "id": "RenderTemplate-C -> END",
            "source": template_c["id"],
            "target": end["id"],
        },
    ]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["outputs"]
    expected_output = {
        start["id"]: {"message": user_input},
        template_a["id"]: {"message": f"A ({user_input})"},
        template_b["id"]: {"message": f"B ({user_input})"},
        template_c["id"]: {"message": f"C (B ({user_input}))"},
        end["id"]: [{"message": f"A ({user_input})"}, {"message": f"C (B ({user_input}))"}],
    }
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_conditional_node(pipeline, experiment_session):
    start = start_node()
    boolean = boolean_node()
    template_true = render_template_node("said hello")
    template_false = render_template_node("didn't say hello, said {{ input }}")
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
            start_node(),
            extract_structured_data_node(str(provider.id), str(provider_model.id), '{"name": "the name of the user"}'),
            end_node(),
        ]
        runnable = create_runnable(pipeline, nodes)
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
            start_node(),
            extract_participant_data_node(
                str(provider.id),
                str(session.experiment.llm_provider_model.id),
                '{"name": "the name of the user"}',
                key_name,
            ),
            end_node(),
        ]
        runnable = create_runnable(pipeline, nodes)
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
    runnable_mock.adapter.get_message_metadata = lambda *args, **kwargs: {"test": "metadata"}
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
            "id": f"{node['id']}->{nodes[i+1]['id']}",
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
            "id": f"{node['id']}->{nodes[i+1]['id']}",
            "source": node["id"],
            "target": nodes[i + 1]["id"],
        }
        for i, node in enumerate(nodes[:2])
    ]
    # Passthrough 2 -> Passthrough 3 -> Passthrough 4
    unreachable_edges = [
        {
            "id": f"{node['id']}->{nodes[i+1]['id']}",
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
