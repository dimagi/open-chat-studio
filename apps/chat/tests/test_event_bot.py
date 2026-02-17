from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from langchain_core.messages import AIMessage

from apps.chat.bots import EventBot
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.tracing import TraceInfo
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def event_bot():
    session = MagicMock(spec=ExperimentSession)
    experiment = MagicMock(spec=Experiment)
    return EventBot(session, experiment, TraceInfo(name="test"))


@patch("apps.chat.bots.get_default_model")
@patch("apps.chat.bots.EventBot.llm_provider", new_callable=PropertyMock)
@patch("apps.chat.bots.PromptTemplateContext.get_context")
def test_get_user_message(mock_get_context, mock_llm_provider, mock_get_default_model, event_bot):
    mock_get_context.return_value = {
        "participant_data": "Test Participant",
        "current_datetime": "2023-10-01 12:00:00",
    }
    mock_llm_provider.return_value = MagicMock()
    mock_service = mock_llm_provider.return_value.get_llm_service.return_value
    mock_llm = mock_service.get_chat_model.return_value
    mock_llm.invoke.return_value = AIMessage(content="Generated message")

    event_prompt = "Test event prompt"
    result = event_bot.get_user_message(event_prompt)

    assert result == "Generated message"
    mock_llm.invoke.assert_called()


@patch("apps.chat.bots.EventBot.get_conversation_history")
@patch("apps.chat.bots.PromptTemplateContext.get_context")
def test_system_prompt(mock_get_context, mock_get_conversation_history, event_bot):
    mock_get_context.return_value = {
        "participant_data": "Test Participant",
        "current_datetime": "2023-10-01 12:00:00",
    }
    mock_get_conversation_history.return_value = "Test history"
    expected_prompt = event_bot.SYSTEM_PROMPT.format(
        participant_data="Test Participant",
        current_datetime="2023-10-01 12:00:00",
        conversation_history="Test history",
    )
    assert event_bot.system_prompt == expected_prompt
    assert "Test history" in event_bot.system_prompt
    assert "Test Participant" in event_bot.system_prompt
    assert "2023-10-01 12:00:00" in event_bot.system_prompt


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_get_user_message_with_llm_provider(mock_get_llm_service):
    fake_llm_service = build_fake_llm_service(responses=["this is a test message"], token_counts=[30, 20, 10])
    mock_get_llm_service.return_value = fake_llm_service
    session = ExperimentSessionFactory()
    LlmProviderFactory(team=session.experiment.team)
    event_bot = EventBot(session, session.experiment, TraceInfo(name="test"))
    response = event_bot.get_user_message("Test event prompt")
    mock_get_llm_service.assert_called()
    assert response == "this is a test message"
    calls = fake_llm_service.llm.get_call_messages()
    assert len(calls) == 1
    assert calls[0][0].type == "system"
    assert session.participant.name in calls[0][0].content
    assert calls[0][1].type == "human"
    assert calls[0][1].content == "Test event prompt"
