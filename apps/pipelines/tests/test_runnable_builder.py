from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import FakeLlm, FakeLlmService
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_full_email_sending_pipeline(get_llm_service, provider):
    service = FakeLlmService(llm=FakeLlm(responses=['{"summary": "Ice is cold"}'], token_counts=[0]))
    get_llm_service.return_value = service

    runnable = PipelineGraph.build_runnable_from_json(
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
    )

    state = PipelineState(
        messages=["Ice is not a liquid. When it is melted it turns into water."], experiment_session_id=1
    )
    runnable.invoke(state)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_send_email():
    runnable = PipelineGraph.build_runnable_from_json(
        {
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
    )
    runnable.invoke(PipelineState(messages=["A cool message"]))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "A cool message"
    assert mail.outbox[0].subject == "This is an interesting email"
    assert mail.outbox[0].to == ["test@example.com"]


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_llm_response(get_llm_service, provider):
    service = FakeLlmService(llm=FakeLlm(responses=["123"], token_counts=[0]))
    get_llm_service.return_value = service
    runnable = PipelineGraph.build_runnable_from_json(
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
    assert runnable.invoke(PipelineState(messages=["Repeat exactly: 123"]))["messages"][-1] == "123"


def test_render_template():
    render_template_node_id = "render-123"
    runnable = PipelineGraph.build_runnable_from_json(
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
    assert runnable.invoke(PipelineState(messages=[{"thing": "Cycling"}]))["messages"][-1] == "Cycling is cool"


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
def test_extract_structured_data_basic(provider):
    fake_llm = FakeLlm(responses=['{"name": "John"}'], token_counts=[0])
    service = FakeLlmService(llm=fake_llm)
    session = ExperimentSessionFactory()

    with (
        mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service),
    ):
        runnable = PipelineGraph.build_runnable_from_json(
            {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "Extract some data",
                            "type": "ExtractStructuredDataBasic",
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
        )
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session_id=session.id)
        assert runnable.invoke(state)["messages"][-1] == '{"name": "John"}'
