from contextlib import contextmanager
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.experiments.models import ParticipantData
from apps.pipelines.flow import FlowNode
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
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
def test_full_email_sending_pipeline(get_llm_service, provider, pipeline):
    service = build_fake_llm_service(responses=['{"summary": "Ice is cold"}'], token_counts=[0])
    get_llm_service.return_value = service

    data = {
        "edges": [
            {
                "id": "report->template",
                "source": "report",
                "target": "template",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "template->email",
                "source": "template",
                "target": "email",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
        "nodes": [
            {
                "data": {
                    "id": "report",
                    "label": "LLM Response with prompt",
                    "type": "LLMResponseWithPrompt",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                        "prompt": """Make a summary of the following text: {input}.
                                Output it as JSON with a single key called 'summary' with the summary.""",
                    },
                },
                "id": "report",
            },
            {
                "data": {
                    "id": "template",
                    "label": "render a template",
                    "type": "RenderTemplate",
                    "params": {
                        "template_string": "<b>{{ summary }}</b>",
                    },
                },
                "id": "template",
            },
            {
                "data": {
                    "id": "email",
                    "label": "Send an email",
                    "type": "SendEmail",
                    "params": {
                        "recipient_list": "test@example.com",
                        "subject": "This is an interesting email",
                    },
                },
                "id": "email",
            },
        ],
        "id": 1,
        "name": "New Pipeline",
    }

    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)

    state = PipelineState(
        messages=["Ice is not a liquid. When it is melted it turns into water."],
        experiment_session_id=1,
    )
    runnable.invoke(state)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_send_email(pipeline):
    data = {
        "edges": [],
        "nodes": [
            {
                "data": {
                    "id": "llm-GUk0C",
                    "label": "Send an email",
                    "type": "SendEmail",
                    "params": {
                        "recipient_list": "test@example.com",
                        "subject": "This is an interesting email",
                    },
                },
                "id": "llm-GUk0C",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    runnable.invoke(PipelineState(messages=["A cool message"]))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_response(get_llm_service, provider, pipeline):
    service = build_fake_llm_service(responses=["123"], token_counts=[0])
    get_llm_service.return_value = service
    data = {
        "edges": [],
        "nodes": [
            {
                "data": {
                    "id": "llm-GUk0C",
                    "label": "Get the robot to respond",
                    "type": "LLMResponse",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                    },
                },
                "id": "llm-GUk0C",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    assert runnable.invoke(PipelineState(messages=["Repeat exactly: 123"]))["messages"][-1] == "123"


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_with_prompt_response(get_llm_service, provider, pipeline, source_material, experiment_session):
    service = build_fake_llm_echo_service()
    get_llm_service.return_value = service

    user_input = "The User Input"
    participant_data = ParticipantData.objects.create(
        team=experiment_session.team,
        content_object=experiment_session.experiment,
        participant=experiment_session.participant,
        data={"name": "A"},
    )
    data = {
        "edges": [
            {
                "id": "llm-1->llm-2",
                "source": "llm-1",
                "target": "llm-2",
                "sourceHandle": "output",
                "targetHandle": "input",
            }
        ],
        "nodes": [
            {
                "data": {
                    "id": "llm-1",
                    "label": "Get the robot to respond",
                    "type": "LLMResponseWithPrompt",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                        "source_material_id": source_material.id,
                        "prompt": (
                            "Node 1: Use this {source_material} to answer questions about {participant_data}."
                            " {input}"
                        ),
                    },
                },
                "id": "llm-1",
            },
            {
                "data": {
                    "id": "llm-1",
                    "label": "Get the robot to respond again",
                    "type": "LLMResponseWithPrompt",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                        "source_material_id": source_material.id,
                        "prompt": "Node 2: ({input})",
                    },
                },
                "id": "llm-2",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    output = runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session))["messages"][
        -1
    ]
    expected_output = (
        f"Node 2: (Node 1: Use this {source_material.material} to answer questions "
        f"about {participant_data.data}. {user_input})"
    )
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_render_template(pipeline):
    render_template_node_id = "render-123"
    data = {
        "edges": [],
        "nodes": [
            {
                "data": {
                    "id": render_template_node_id,
                    "label": "RenderTemplate",
                    "type": "RenderTemplate",
                    "params": {
                        "template_string": "{{ thing }} is cool",
                    },
                },
                "id": "llm-GUk0C",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    assert runnable.invoke(PipelineState(messages=[{"thing": "Cycling"}]))["messages"][-1] == "Cycling is cool"


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_branching_pipeline(pipeline, experiment_session):
    data = {
        "edges": [
            {
                "id": "START -> RenderTemplate-A",
                "source": "Passthrough-1",
                "target": "RenderTemplate-A",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "Passthrough -> RenderTemplate-B",
                "source": "Passthrough-1",
                "target": "RenderTemplate-B",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "RenderTemplate-A -> END",
                "source": "RenderTemplate-A",
                "target": "Passthrough-2",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "RenderTemplate-B -> RenderTemplate-C",
                "source": "RenderTemplate-B",
                "target": "RenderTemplate-C",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "RenderTemplate-C -> END",
                "source": "RenderTemplate-C",
                "target": "Passthrough-2",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
        "nodes": [
            {
                "id": "Passthrough-1",
                "data": {
                    "id": "Passthrough-1",
                    "type": "Passthrough",
                    "label": "Do Nothing",
                    "params": {},
                    "inputParams": [],
                },
                "type": "pipelineNode",
                "position": {"x": 76.27754748414293, "y": 280.32562971844055},
            },
            {
                "id": "RenderTemplate-B",
                "data": {
                    "id": "RenderTemplate-B",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "B ({{ input}})"},
                    "inputParams": [{"name": "template_string", "type": "PipelineJinjaTemplate"}],
                },
                "type": "pipelineNode",
            },
            {
                "id": "Passthrough-2",
                "data": {
                    "id": "Passthrough-2",
                    "type": "Passthrough",
                    "label": "Do Nothing",
                    "params": {},
                    "inputParams": [],
                },
                "type": "pipelineNode",
            },
            {
                "id": "RenderTemplate-C",
                "data": {
                    "id": "RenderTemplate-C",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "C ({{input }})"},
                    "inputParams": [{"name": "template_string", "type": "PipelineJinjaTemplate"}],
                },
                "type": "pipelineNode",
            },
            {
                "id": "RenderTemplate-A",
                "data": {
                    "id": "RenderTemplate-A",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "A ({{ input }})"},
                    "inputParams": [{"name": "template_string", "type": "PipelineJinjaTemplate"}],
                },
                "type": "pipelineNode",
            },
        ],
    }
    user_input = "The Input"
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
    output = runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session))["outputs"]
    expected_output = {
        "Passthrough-1": user_input,
        "RenderTemplate-A": f"A ({user_input})",
        "RenderTemplate-B": f"B ({user_input})",
        "RenderTemplate-C": f"C (B ({user_input}))",
        "Passthrough-2": [f"A ({user_input})", f"C (B ({user_input}))"],
    }
    assert output == expected_output


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_conditional_node(pipeline, experiment_session):
    data = {
        "edges": [
            {
                "id": "Boolean -> True",
                "source": "BooleanNode",
                "target": "RenderTemplate-true",
                "sourceHandle": "output_true",
                "targetHandle": "input",
            },
            {
                "id": "Boolean -> False",
                "source": "BooleanNode",
                "target": "RenderTemplate-false",
                "sourceHandle": "output_false",
                "targetHandle": "input",
            },
            {
                "id": "False -> End",
                "source": "RenderTemplate-false",
                "target": "End",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "True -> End",
                "source": "RenderTemplate-true",
                "target": "End",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
        "nodes": [
            {
                "id": "BooleanNode",
                "data": {
                    "id": "BooleanNode",
                    "type": "BooleanNode",
                    "label": "Boolean Node",
                    "params": {"input_equals": "hello"},
                    "inputParams": [{"name": "input_equals", "type": "<class 'str'>"}],
                },
                "type": "pipelineNode",
            },
            {
                "id": "RenderTemplate-true",
                "data": {
                    "id": "RenderTemplate-true",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "said hello"},
                    "inputParams": [{"name": "template_string", "type": "PipelineJinjaTemplate"}],
                },
                "type": "pipelineNode",
            },
            {
                "id": "RenderTemplate-false",
                "data": {
                    "id": "RenderTemplate-false",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "didn't say hello, said {{ input }}"},
                    "inputParams": [{"name": "template_string", "type": "PipelineJinjaTemplate"}],
                },
                "type": "pipelineNode",
            },
            {
                "id": "End",
                "data": {"id": "End", "type": "Passthrough", "label": "End", "params": {}, "inputParams": []},
                "type": "pipelineNode",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)

    output = runnable.invoke(PipelineState(messages=["hello"], experiment_session=experiment_session))
    assert output["messages"][-1] == "said hello"
    assert "RenderTemplate-false" not in output["outputs"]

    output = runnable.invoke(PipelineState(messages=["bad"], experiment_session=experiment_session))
    assert output["messages"][-1] == "didn't say hello, said bad"
    assert "RenderTemplate-true" not in output["outputs"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_router_node(get_llm_service, provider, pipeline, experiment_session):
    service = build_fake_llm_echo_service()
    get_llm_service.return_value = service

    data = {
        "edges": [
            {
                "id": "RouterNode -> A",
                "source": "RouterNode",
                "target": "A",
                "sourceHandle": "output_0",
                "targetHandle": "input",
            },
            {
                "id": "RouterNode -> B",
                "source": "RouterNode",
                "target": "B",
                "sourceHandle": "output_1",
                "targetHandle": "input",
            },
            {
                "id": "RouterNode -> C",
                "source": "RouterNode",
                "target": "C",
                "sourceHandle": "output_2",
                "targetHandle": "input",
            },
            {
                "id": "A -> END",
                "source": "A",
                "target": "END",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "B -> END",
                "source": "B",
                "target": "END",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "C -> END",
                "source": "C",
                "target": "END",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "RouterNode -> D",
                "source": "RouterNode",
                "target": "D",
                "sourceHandle": "output_3",
                "targetHandle": "input",
            },
            {
                "id": "D -> END",
                "source": "D",
                "target": "END",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
        "nodes": [
            {
                "id": "RouterNode",
                "data": {
                    "id": "RouterNode",
                    "type": "RouterNode",
                    "label": "Router",
                    "params": {
                        "prompt": "{ input }",
                        "keyword_0": "A",
                        "keyword_1": "b",
                        "keyword_2": "c",
                        "keyword_3": "d",
                        "llm_model": "claude-3-5-sonnet-20240620",
                        "num_outputs": "4",
                        "llm_provider_id": provider.id,
                    },
                },
                "type": "pipelineNode",
            },
            {
                "id": "A",
                "data": {
                    "id": "A",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "A {{input }}"},
                },
                "type": "pipelineNode",
            },
            {
                "id": "B",
                "data": {
                    "id": "B",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "B {{ input }}"},
                },
                "type": "pipelineNode",
            },
            {
                "id": "C",
                "data": {
                    "id": "C",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "C {{ input }}"},
                },
                "type": "pipelineNode",
            },
            {
                "id": "D",
                "data": {
                    "id": "D",
                    "type": "RenderTemplate",
                    "label": "Render a template",
                    "params": {"template_string": "D {{ input }}"},
                },
                "type": "pipelineNode",
            },
            {
                "id": "END",
                "data": {
                    "id": "END",
                    "type": "Passthrough",
                    "label": "Do Nothing",
                    "params": {},
                },
                "type": "pipelineNode",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)

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
def extract_structured_data_pipeline(provider, pipeline, llm=None):
    service = build_fake_llm_service(responses=[{"name": "John"}], token_counts=[0], fake_llm=llm)

    with (
        mock.patch(
            "apps.service_providers.models.LlmProvider.get_llm_service",
            return_value=service,
        ),
    ):
        data = {
            "edges": [],
            "nodes": [
                {
                    "data": {
                        "id": "llm-GUk0C",
                        "label": "Extract some data",
                        "type": "ExtractStructuredData",
                        "params": {
                            "llm_provider_id": provider.id,
                            "llm_model": "fake-model",
                            "data_schema": '{"name": "the name of the user"}',
                        },
                    },
                    "id": "llm-GUk0C",
                },
            ],
        }
        pipeline.data = data
        pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
        runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
        yield runnable


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_no_chunking(provider, pipeline):
    session = ExperimentSessionFactory()

    with extract_structured_data_pipeline(provider, pipeline) as graph:
        state = PipelineState(
            messages=["ai: hi user\nhuman: hi there I am John"],
            experiment_session=session,
        )
        assert graph.invoke(state)["messages"][-1] == '{"name": "John"}'


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_with_chunking(provider, pipeline):
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
        extract_structured_data_pipeline(provider, pipeline, llm) as graph,
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
        key_name=None,
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
        data = {
            "edges": [],
            "nodes": [
                {
                    "data": {
                        "id": "extraction",
                        "label": "Extract some data",
                        "type": "ExtractParticipantData",
                        "params": {
                            "llm_provider_id": provider.id,
                            "llm_model": "fake-model",
                            "data_schema": '{"name": "the name of the user"}',
                            "key_name": key_name,
                        },
                    },
                    "id": "extraction",
                }
            ],
            "id": 1,
            "name": "New Pipeline",
        }
        pipeline.data = data
        pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
        runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session=session)
        runnable.invoke(state)
