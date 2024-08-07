from contextlib import contextmanager
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.experiments.models import ParticipantData
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import FakeLlmSimpleTokenCount, build_fake_llm_service
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_full_email_sending_pipeline(get_llm_service, provider):
    service = build_fake_llm_service(responses=['{"summary": "Ice is cold"}'], token_counts=[0])
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
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
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
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_llm_response(get_llm_service, provider):
    service = build_fake_llm_service(responses=["123"], token_counts=[0])
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


@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
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


@contextmanager
def extract_structured_data_pipeline(provider, llm=None):
    service = build_fake_llm_service(responses=[{"name": "John"}], token_counts=[0], fake_llm=llm)

    with (
        mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service),
    ):
        graph = PipelineGraph.build_runnable_from_json(
            {
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
        )
        yield graph


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_no_chunking(provider):
    session = ExperimentSessionFactory()

    with extract_structured_data_pipeline(provider) as graph:
        state = PipelineState(messages=["ai: hi user\nhuman: hi there I am John"], experiment_session=session)
        assert graph.invoke(state)["messages"][-1] == '{"name": "John"}'


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_structured_data_with_chunking(provider):
    session = ExperimentSessionFactory()
    ParticipantData.objects.create(
        team=session.team, content_object=session.experiment, data={"drink": "martini"}, participant=session.participant
    )
    llm = FakeLlmSimpleTokenCount(
        responses=[
            {"name": None},  # the first chunk sees nothing of value
            {"name": "james"},  # the second chunk message sees the name
            {"name": "james"},  # the third chunk sees nothing of value
        ]
    )

    with (
        extract_structured_data_pipeline(provider, llm) as graph,
        mock.patch(
            "apps.pipelines.nodes.nodes.ExtractStructuredData.chunk_messages",
            return_value=["I am bond", "james bond", "007"],
        ),
    ):
        state = PipelineState(messages=["ai: hi user\nhuman: hi there I am John"], experiment_session=session)
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
def test_extract_participant_data(provider):
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
        session, provider=provider, extracted_data={"name": "Johnny"}, key_name="profile"
    )

    participant_data = ParticipantData.objects.for_experiment(session.experiment).get(participant=session.participant)
    assert participant_data.data == {"profile": {"name": "Johnny"}}

    # The "profile" key should be updated
    _run_data_extract_and_update_pipeline(
        session, provider=provider, extracted_data={"name": "John", "last_name": "Wick"}, key_name="profile"
    )
    participant_data.refresh_from_db()
    assert participant_data.data == {"profile": {"name": "John", "last_name": "Wick"}}

    # New data should be inserted at the toplevel
    _run_data_extract_and_update_pipeline(session, provider=provider, extracted_data={"has_pets": False}, key_name=None)
    participant_data.refresh_from_db()
    assert participant_data.data == {"profile": {"name": "John", "last_name": "Wick"}, "has_pets": False}


def _run_data_extract_and_update_pipeline(session, provider, extracted_data: dict, key_name: str):
    service = build_fake_llm_service(responses=[extracted_data], token_counts=[0])

    with (
        mock.patch("apps.service_providers.models.LlmProvider.get_llm_service", return_value=service),
    ):
        runnable = PipelineGraph.build_runnable_from_json(
            {
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
        )
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session=session)
        runnable.invoke(state)
