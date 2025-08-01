from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat.bots import TopicBot
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentRoute, ExperimentRouteType
from apps.service_providers.tests.mock_tracer import MockTracer
from apps.service_providers.tracing import TracingService
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.langchain import build_fake_llm_service, mock_llm


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("with_default", "routing_response", "expected_tag"),
    [
        (True, "keyword1", "keyword1"),
        (True, "keyword3", "keyword3"),
        (True, "not a valid keyword", "keyword2"),
        (False, "keyword2", "keyword2"),
        (False, "not a valid keyword", "keyword1"),
    ],
)
def test_experiment_routing(with_default, routing_response, expected_tag):
    experiment = _make_experiment_with_routing(with_default)
    session = ExperimentSessionFactory(experiment=experiment)
    fake_service = build_fake_llm_service(responses=[routing_response, "How can I help today?"], token_counts=[0])
    with patch("apps.experiments.models.Experiment.get_llm_service", new=lambda x: fake_service):
        bot = TopicBot(session, experiment, TracingService.empty())
        response = bot.process_input("Hi")
    assert response.content == "How can I help today?"
    assert bot.processor_experiment == ExperimentRoute.objects.get(parent=experiment, keyword=expected_tag).child
    assert fake_service.llm.get_call_messages() == [
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
    ]
    message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert set(message.tags.values_list("name", flat=True)) == set([expected_tag, "v1-unreleased"])


@pytest.mark.django_db()
def test_experiment_routing_with_tracing():
    experiment = _make_experiment_with_routing()
    session = ExperimentSessionFactory(experiment=experiment)
    trace_service = TracingService([MockTracer()], experiment.id, experiment.team_id)
    with (
        trace_service.trace("test", session),
        mock_llm(responses=["anything", "How can I help today?"], token_counts=[0]) as fake_service,
    ):
        bot = TopicBot(session, experiment, trace_service)
        response = bot.process_input("Hi")
    assert response.content == "How can I help today?"
    assert fake_service.llm.get_call_messages() == [
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
    ]
    human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).first()
    assert "trace_info" in human_message.metadata
    ai_message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert "trace_info" in ai_message.metadata


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("with_default", "routing_response", "expected_tag"),
    [
        (True, "keyword1", "keyword1"),
        (True, "keyword3", "keyword3"),
        (True, "not a valid keyword", "keyword2"),
        (False, "keyword2", "keyword2"),
        (False, "not a valid keyword", "keyword1"),
    ],
)
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_response_with_retries")
def test_experiment_routing_with_assistant(
    get_response_with_retries, save_response_annotations, with_default, routing_response, expected_tag
):
    response = Mock()
    response.thread_id = "thread_id"
    get_response_with_retries.return_value = "tread_id", "run_id"
    save_response_annotations.return_value = ("How can I help today?", [])

    experiment = _make_experiment_with_routing(with_default=with_default, assistant_children=True)
    session = ExperimentSessionFactory(experiment=experiment)
    fake_service = build_fake_llm_service(responses=[routing_response], token_counts=[0])

    with patch("apps.experiments.models.Experiment.get_llm_service", new=lambda x: fake_service):
        bot = TopicBot(session, experiment, TracingService.empty())
        response = bot.process_input("Hi")
    assert response.content == "How can I help today?"

    assert bot.processor_experiment == ExperimentRoute.objects.get(parent=experiment, keyword=expected_tag).child

    message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert set(message.tags.values_list("name", flat=True)) == set([expected_tag, "v1-unreleased"])


def _make_experiment_with_routing(with_default=True, assistant_children=False, with_terminal=False):
    team = TeamFactory()
    experiments = ExperimentFactory.create_batch(5 if with_terminal else 4, team=team)
    router = experiments[0]
    if assistant_children:
        for exp in experiments[1:]:
            exp.assistant = OpenAiAssistantFactory(team=team)
            exp.save()

    children = [
        ExperimentRoute(team=team, parent=router, child=experiments[1], keyword="keyword1", is_default=False),
        ExperimentRoute(
            # make the middle one the default to avoid first / last false positives
            team=team,
            parent=router,
            child=experiments[2],
            keyword="keyword2",
            is_default=with_default,
        ),
        ExperimentRoute(team=team, parent=router, child=experiments[3], keyword="keyword3", is_default=False),
    ]
    if with_terminal:
        children.append(
            ExperimentRoute(team=team, parent=router, child=experiments[4], type=ExperimentRouteType.TERMINAL)
        )
    ExperimentRoute.objects.bulk_create(children)

    return router
