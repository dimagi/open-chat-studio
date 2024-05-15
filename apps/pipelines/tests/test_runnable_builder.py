from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.utils import build_runnable_from_graph
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import FakeLlm, FakeLlmService


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_full_email_sending_pipeline(provider):
    graph = PipelineGraph.from_json(
        {
            "edges": [
                {
                    "id": "report->template",
                    "source": "report",
                    "target": "template",
                },
                {
                    "id": "template->email",
                    "source": "template",
                    "target": "email",
                },
            ],
            "nodes": [
                {
                    "data": {
                        "id": "report",
                        "label": "Create the report",
                        "type": "CreateReport",
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
                            "recipient_list": ["test@example.com"],
                            "subject": "This is an interesting email",
                        },
                    },
                    "id": "email",
                },
            ],
            "id": 1,
            "name": "New Pipeline",
        }
    )
    service = FakeLlmService(llm=FakeLlm(responses=['{"summary": "Ice is cold"}'], token_counts=[0]))
    with mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service):
        runnable = build_runnable_from_graph(graph)
    runnable.invoke(
        {"input": "Ice is not a liquid. When it is melted it turns into water."},
    )
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_send_email():
    graph = PipelineGraph.from_json(
        {
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
    )
    runnable = build_runnable_from_graph(graph)
    runnable.invoke(
        "A cool message",
    )
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@pytest.mark.django_db()
def test_llm_response(provider):
    graph = PipelineGraph.from_json(
        {
            "edges": [],
            "nodes": [
                {
                    "data": {
                        "id": "llm-GUk0C",
                        "label": "Get the robot to respond",
                        "type": "LLMResponse",
                        "params": {"llm_provider_id": provider.id, "llm_model": "fake-model"},
                    },
                    "id": "llm-GUk0C",
                },
            ],
        }
    )
    service = FakeLlmService(llm=FakeLlm(responses=["123"], token_counts=[0]))
    with mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service):
        runnable = build_runnable_from_graph(graph)
    assert runnable.invoke("Repeat exactly: 123").content == "123"


def test_render_template():
    render_template_node_id = "render-123"
    graph = PipelineGraph.from_json(
        {
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
    )
    runnable = build_runnable_from_graph(graph)
    assert runnable.invoke({"thing": "Cycling"}) == "Cycling is cool"
