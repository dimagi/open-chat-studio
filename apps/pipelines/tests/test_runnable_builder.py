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
        assert graph.invoke(state)["messages"][-1] == {"name": "John"}


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
            {"name": None},  # the third chunk sees nothing of value
            {"name": "james"},  # the first + second chunk outputs the name
            {"name": "james"},  # the output of that + no new data still outputs the name
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

    # The problem is that the participant data is only in the first inference call. What if we need it in the 3rd
    # call?

    # This is what the LLM sees. First extract data
    inferences = llm.get_call_messages()
    assert inferences[0][0].text == "Current user data: {'drink': 'martini'}\n Conversations: I am bond"
    assert inferences[1][0].text == "Current user data: {'drink': 'martini'}\n Conversations: james bond"
    assert inferences[2][0].text == "Current user data: {'drink': 'martini'}\n Conversations: 007"
    # Then merge
    assert inferences[3][0].text == "Current user data: {'name': None}\n Conversations: {'name': 'james'}"
    assert inferences[4][0].text == "Current user data: {'name': 'james'}\n Conversations: {'name': None}"

    # Expected node output
    assert extracted_data == {"name": "james"}


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
@pytest.mark.parametrize(
    ("key_name", "input", "existing_data", "expected_data_structure", "expect_error"),
    [
        (None, {"cats": "yes", "name": "John"}, None, {"cats": "yes", "name": "John"}, False),
        (None, {"cats": "yes", "name": "John"}, {}, {"cats": "yes", "name": "John"}, False),
        ("pets", {"cats": []}, {}, {"pets": {"cats": []}}, False),
        ("pets", {"cats": []}, {"pets": {"dogs": []}}, {"pets": {"cats": []}}, False),
        ("name", "John", {}, {"name": "John"}, False),
        ("name", "John", {"name": "Johnny", "surname": "Wick"}, {"name": "John", "surname": "Wick"}, False),
        ("profiles", [{"name": "John"}], {}, {"profiles": [{"name": "John"}]}, False),
        ("profiles", [{"name": "John"}], {"profiles": "there are none"}, {"profiles": [{"name": "John"}]}, False),
        (None, "some input", {}, None, True),
        (None, [], {}, None, True),
    ],
)
def test_update_participant_data_node(key_name, input, existing_data, expected_data_structure, expect_error):
    session = ExperimentSessionFactory()
    if existing_data:
        ParticipantData.objects.create(
            team=session.team, participant=session.participant, data=existing_data, content_object=session.experiment
        )

    runnable = PipelineGraph.build_runnable_from_json(
        {
            "edges": [],
            "nodes": [
                {
                    "data": {
                        "id": "llm-GUk0C",
                        "label": "Update participant memory",
                        "type": "UpdateParticipantMemory",
                        "params": {
                            "key_name": key_name,
                        },
                    },
                    "id": "llm-GUk0C",
                },
            ],
        }
    )
    state = PipelineState(messages=[input], experiment_session=session)
    if expect_error:
        with pytest.raises(KeyError, match="A key is expected for a string or list value."):
            runnable.invoke(state)
    else:
        runnable.invoke(state)
        participant_data = ParticipantData.objects.get(participant=session.participant)
        assert participant_data.data == expected_data_structure


@django_db_with_data(available_apps=("apps.service_providers", "apps.experiments"))
@mock.patch("apps.pipelines.nodes.base.PipelineNode.logger", mock.Mock())
def test_extract_and_update_data_pipeline(provider):
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
                "edges": [
                    {
                        "id": "extraction->update_data",
                        "source": "extraction",
                        "target": "update_data",
                    },
                ],
                "nodes": [
                    {
                        "data": {
                            "id": "extraction",
                            "label": "Extract some data",
                            "type": "ExtractStructuredData",
                            "params": {
                                "llm_provider_id": provider.id,
                                "llm_model": "fake-model",
                                "data_schema": '{"name": "the name of the user"}',
                            },
                        },
                        "id": "extraction",
                    },
                    {
                        "data": {
                            "id": "update_data",
                            "label": "Update participant memory",
                            "type": "UpdateParticipantMemory",
                            "params": {
                                "key_name": key_name,
                            },
                        },
                        "id": "update_data",
                    },
                ],
                "id": 1,
                "name": "New Pipeline",
            }
        )
        state = PipelineState(messages=["ai: hi user\nhuman: hi there"], experiment_session=session)
        runnable.invoke(state)
