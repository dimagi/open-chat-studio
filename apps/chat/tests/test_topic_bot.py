from unittest.mock import patch

import pytest

from apps.annotations.models import TagCategories
from apps.chat.bots import TopicBot
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentRoute, ExperimentRouteType, SafetyLayer
from apps.service_providers.models import TraceProvider
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm


@pytest.mark.django_db()
@patch("apps.chat.bots.SafetyBot.is_safe")
def test_safety_response(is_safe_mock):
    is_safe_mock.return_value = False

    session = ExperimentSessionFactory()
    experiment = session.experiment
    layer = SafetyLayer.objects.create(
        prompt_text="Is this message safe?", team=experiment.team, prompt_to_bot="Unsafe reply"
    )
    experiment.safety_layers.add(layer)

    expected = "Sorry I can't help with that."
    bot = TopicBot(session)
    with patch.object(TopicBot, "_get_safe_response", wraps=bot._get_safe_response) as mock_get_safe_response:
        with mock_experiment_llm(experiment, responses=[expected]):
            response = bot.process_input("It's my way or the highway!")

    mock_get_safe_response.assert_called()
    assert response == expected


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.runnables.SimpleExperimentRunnable._get_output_check_cancellation")
def test_bot_with_terminal_bot(get_output_check_cancellation):
    get_output_check_cancellation.side_effect = ["let's barbecue!", "kom ons braai!"]
    session = ExperimentSessionFactory()
    experiment = session.experiment
    ExperimentRoute.objects.create(
        team=experiment.team,
        parent=experiment,
        child=ExperimentFactory(team=experiment.team),
        type=ExperimentRouteType.TERMINAL,
    )

    expected = "Sorry I can't help with that."
    bot = TopicBot(session)
    with mock_experiment_llm(experiment, responses=[expected]):
        bot.process_input("What are we going to do?")

    assert session.chat.messages.count() == 2
    assert session.chat.messages.get(message_type="human").content == "What are we going to do?"
    assert session.chat.messages.get(message_type="ai").content == "kom ons braai!"


@pytest.mark.django_db()
def test_get_safe_response_creates_ai_message_for_default_messages():
    session = ExperimentSessionFactory()
    layer = SafetyLayer.objects.create(prompt_text="Is this message safe?", team=session.experiment.team)
    session.experiment.safety_layers.add(layer)

    bot = TopicBot(session)
    bot._get_safe_response(layer)
    message = ChatMessage.objects.get(chat__team=session.team, message_type=ChatMessageType.AI)
    assert message.content == "Sorry, I can't answer that. Please try something else."
    assert message.tags.get(category=TagCategories.SAFETY_LAYER_RESPONSE) is not None


@pytest.mark.django_db()
def test_tracing_service():
    session = ExperimentSessionFactory()
    provider = TraceProvider(type="langfuse", config={})
    session.experiment.trace_provider = provider
    service = "apps.service_providers.tracing.service.LangFuseTraceService"
    with (
        patch(f"{service}.get_callback") as mock_get_callback,
        patch(f"{service}.get_current_trace_info") as mock_get_trace_info,
        mock_experiment_llm(None, responses=["response"]),
    ):
        bot = TopicBot(session)
        assert bot.process_input("test") == "response"
        mock_get_callback.assert_called_once_with(
            participant_id=session.participant.identifier, session_id=str(session.external_id)
        )
        assert mock_get_trace_info.call_count == 2
    bot.process_input("test")
