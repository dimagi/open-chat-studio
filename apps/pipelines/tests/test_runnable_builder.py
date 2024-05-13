from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings
from langchain_core.runnables import RunnableConfig

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.utils import build_runnable_from_graph
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import FakeLlm, FakeLlmService


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_full_email_sending_pipeline(session):
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "Create the report",
                            "type": "CreateReport",
                            "params": {
                                "prompt": """Make a summary of the following text: {input}.
                                Output it as JSON with a single key called 'summary' with the summary.""",
                            },
                        },
                        "id": "llm-GUk0C",
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
                        "id": "llm-GUk0C",
                    },
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "Send an email",
                            "type": "SendEmail",
                            "params": {
                                "recipient_list": ["test@example.com"],
                                "subject": "This is an interesting email",
                            },
                        },
                        "id": "llm-GUk0C",
                    },
                ],
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    service = FakeLlmService(llm=FakeLlm(responses=['{"summary": "Ice is cold"}'], token_counts=[0]))
    with mock.patch("apps.experiments.models.Experiment.get_llm_service", return_value=service):
        runnable = build_runnable_from_graph(graph, session_id=session.id)
    runnable.invoke({"input": "Ice is not a liquid. When it is melted it turns into water."})
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_send_email():
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "Send an email",
                            "type": "SendEmail",
                            "params": {
                                "recipient_list": ["test@example.com"],
                                "subject": "This is an interesting email",
                            },
                        },
                        "id": "llm-GUk0C",
                    },
                ],
            }
        }
    )
    runnable = build_runnable_from_graph(graph)
    runnable.invoke("A cool message")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@pytest.mark.django_db()
def test_llm_response(session):
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "Get the robot to respond",
                            "type": "LLMResponse",
                        },
                        "id": "llm-GUk0C",
                    },
                ],
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    with pytest.raises(ValueError, match="session_id"):
        build_runnable_from_graph(graph)
    service = FakeLlmService(llm=FakeLlm(responses=["123"], token_counts=[0]))
    with mock.patch("apps.experiments.models.Experiment.get_llm_service", return_value=service):
        runnable = build_runnable_from_graph(graph, session_id=session.id)
    assert runnable.invoke("Repeat exactly: 123").content == "123"


def test_render_template():
    render_template_node_id = "render-123"
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": render_template_node_id,
                            "label": "RenderTemplate",
                            "type": "RenderTemplate",
                            "params": {
                                "template_string": "{{ stuff }} is cool",
                            },
                        },
                        "id": "llm-GUk0C",
                    },
                ],
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    runnable = build_runnable_from_graph(graph)

    assert runnable.invoke({"stuff": "Elephants"}) == "Elephants is cool"
    assert (
        runnable.invoke(
            {"stuff": "elephant"},
            config=RunnableConfig(configurable={f"template_string_{render_template_node_id}": "Hello {{stuff }}"}),
        )
        == "Hello elephant"
    )
