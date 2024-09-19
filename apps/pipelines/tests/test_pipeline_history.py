from unittest import mock

import pytest

from apps.pipelines.flow import FlowNode
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import PipelineChatHistory
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import (
    FakeLlmEcho,
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
def experiment_session():
    return ExperimentSessionFactory()


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_with_node_history(get_llm_service, provider, pipeline, experiment_session):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service

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
                        "history_type": "node",
                        "prompt": "Node 1:",
                    },
                },
                "id": "llm-1",
            },
            {
                "data": {
                    "id": "llm-2",
                    "label": "Get the robot to respond again",
                    "type": "LLMResponseWithPrompt",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                        "prompt": "Node 2:",
                        # No history_type
                    },
                },
                "id": "llm-2",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)

    user_input = "The User Input"
    runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session))["messages"][-1]
    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
    ]
    assert [
        [(message.type, message.content) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages

    history = PipelineChatHistory.objects.get(session=experiment_session.id, name="llm-1")
    assert history.type == "node"
    assert history.messages.count() == 1
    assert history.messages.first().as_tuples() == [("human", user_input), ("ai", f"Node 1: {user_input}")]

    history_2 = PipelineChatHistory.objects.filter(session=experiment_session.id, name="llm-2").count()
    assert history_2 == 0

    user_input_2 = "Saying more stuff"
    output_2 = runnable.invoke(PipelineState(messages=[user_input_2], experiment_session=experiment_session))[
        "messages"
    ][-1]

    expected_output = f"Node 2: Node 1: {user_input_2}"
    assert output_2 == expected_output

    assert history.messages.count() == 2
    new_messages = history.messages.last().as_tuples()
    assert new_messages == [("human", user_input_2), ("ai", f"Node 1: {user_input_2}")]

    history_2 = PipelineChatHistory.objects.filter(session=experiment_session.id, name="llm-2").count()
    assert history_2 == 0

    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
        [
            ("system", "Node 1:"),
            ("human", user_input),
            ("ai", f"Node 1: {user_input}"),
            ("human", user_input_2),
        ],  # History is inserted correctly for Node 1.
        [("system", "Node 2:"), ("human", f"Node 1: {user_input_2}")],
    ]
    assert [
        [(message.type, message.content) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_with_multiple_node_histories(get_llm_service, provider, pipeline, experiment_session):
    llm = FakeLlmEcho()
    service = build_fake_llm_service(None, [0], llm)
    get_llm_service.return_value = service

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
                        "history_type": "node",
                        "prompt": "Node 1:",
                    },
                },
                "id": "llm-1",
            },
            {
                "data": {
                    "id": "llm-2",
                    "label": "Get the robot to respond again",
                    "type": "LLMResponseWithPrompt",
                    "params": {
                        "llm_provider_id": provider.id,
                        "llm_model": "fake-model",
                        "prompt": "Node 2:",
                        "history_type": "node",
                    },
                },
                "id": "llm-2",
            },
        ],
    }
    pipeline.data = data
    pipeline.set_nodes([FlowNode(**node) for node in data["nodes"]])
    runnable = PipelineGraph.build_runnable_from_pipeline(pipeline)

    user_input = "The User Input"
    output_1 = runnable.invoke(PipelineState(messages=[user_input], experiment_session=experiment_session))["messages"][
        -1
    ]
    expected_output = f"Node 2: Node 1: {user_input}"
    assert output_1 == expected_output

    history = PipelineChatHistory.objects.get(session=experiment_session.id, name="llm-1")
    assert history.type == "node"
    assert history.messages.count() == 1
    assert history.messages.first().as_tuples() == [("human", user_input), ("ai", f"Node 1: {user_input}")]

    history_2 = PipelineChatHistory.objects.get(session=experiment_session.id, name="llm-2")
    assert history_2.type == "node"
    assert history_2.messages.count() == 1
    assert history_2.messages.first().as_tuples() == [
        ("human", f"Node 1: {user_input}"),
        ("ai", expected_output),
    ]

    user_input_2 = "Saying more stuff"
    output_2 = runnable.invoke(PipelineState(messages=[user_input_2], experiment_session=experiment_session))[
        "messages"
    ][-1]
    expected_output = f"Node 2: Node 1: {user_input_2}"
    assert output_2 == expected_output

    assert history.messages.count() == 2
    new_messages = history.messages.last().as_tuples()
    assert new_messages == [("human", user_input_2), ("ai", f"Node 1: {user_input_2}")]
    assert history_2.messages.count() == 2
    new_messages_2 = history_2.messages.last().as_tuples()
    assert new_messages_2 == [("human", f"Node 1: {user_input_2}"), ("ai", output_2)]

    expected_call_messages = [
        [("system", "Node 1:"), ("human", user_input)],
        [("system", "Node 2:"), ("human", f"Node 1: {user_input}")],
        [
            ("system", "Node 1:"),
            ("human", user_input),
            ("ai", f"Node 1: {user_input}"),
            ("human", user_input_2),
        ],  # History from node 1 is inserted
        [
            ("system", "Node 2:"),
            ("human", f"Node 1: {user_input}"),
            ("ai", f"Node 2: Node 1: {user_input}"),
            ("human", f"Node 1: {user_input_2}"),
        ],  # History from node 2 is inserted
    ]
    assert [
        [(message.type, message.content) for message in call] for call in llm.get_call_messages()
    ] == expected_call_messages
