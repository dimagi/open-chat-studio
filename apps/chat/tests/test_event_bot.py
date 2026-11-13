from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from apps.chat.bots import EventBot
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.llm_service.default_models import get_default_model
from apps.service_providers.tracing import TraceInfo
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import build_fake_llm_service


@pytest.fixture()
def event_bot():
    session = MagicMock(spec=ExperimentSession)
    experiment = MagicMock(spec=Experiment)
    return EventBot(session, experiment, TraceInfo(name="test"))


@patch("apps.chat.bots.EventBot.get_provider_and_model")
@patch("apps.chat.bots.PromptTemplateContext.get_context")
def test_get_user_message(mock_get_context, mock_get_provider_and_model, event_bot):
    mock_get_context.return_value = {
        "participant_data": "Test Participant",
        "current_datetime": "2023-10-01 12:00:00",
    }
    provider, model = MagicMock(), MagicMock()
    model.name = "test-model"
    mock_get_provider_and_model.return_value = (provider, model)
    mock_service = provider.get_llm_service.return_value
    mock_llm = mock_service.get_chat_model.return_value
    mock_llm.invoke.return_value = AIMessage(content="Generated message")

    event_prompt = "Test event prompt"
    result = event_bot.get_user_message(event_prompt)

    assert result == "Generated message"
    mock_llm.invoke.assert_called()
    assert mock_service.get_chat_model.call_args.args == ("test-model",)


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
    fake_llm_service = build_fake_llm_service(responses=["this is a test message"])
    mock_get_llm_service.return_value = fake_llm_service
    session = ExperimentSessionFactory.create()
    LlmProviderFactory.create(team=session.experiment.team)
    LlmProviderModelFactory.create(team=session.experiment.team, type="openai")
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


@pytest.mark.django_db()
def test_event_bot_history_skips_a_marked_human_message():
    session = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="fine")
    filtered = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="bad")
    filtered.metadata[ChatMessageMetadataKeys.MODEL_REFUSED] = {"kind": "refusal", "provider_reason": "r"}
    filtered.save(update_fields=["metadata"])
    bot = EventBot(session, session.experiment, TraceInfo(name="test"))

    history = bot.get_conversation_history()

    assert "fine" in history
    assert "bad" not in history


FAILED = {"verified_credentials": False, "verification_error": "NotFound: 404"}
VERIFIED = {"verified_credentials": True}
UNCHECKED = {}


def _event_bot(session):
    return EventBot(session, session.experiment, TraceInfo(name="test"))


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("states", "expected_index"),
    [
        pytest.param([FAILED, UNCHECKED, VERIFIED], 2, id="verified-over-unchecked-and-failed"),
        pytest.param([FAILED, UNCHECKED, UNCHECKED], 1, id="first-unchecked-when-none-verified"),
        pytest.param([FAILED, FAILED], 0, id="first-provider-when-all-failed"),
        pytest.param([VERIFIED, VERIFIED], 0, id="first-verified-when-several"),
    ],
)
def test_provider_prefers_verified_then_unfailed(states, expected_index):
    session = ExperimentSessionFactory.create()
    team = session.experiment.team
    LlmProviderModelFactory.create(team=team, type="openai")
    providers = [
        LlmProviderFactory.create(team=team, name=f"provider {i}", extra_data=extra_data)
        for i, extra_data in enumerate(states)
    ]

    provider, _model = _event_bot(session).get_provider_and_model()

    assert provider == providers[expected_index]


@pytest.mark.django_db()
def test_provider_without_a_chat_model_is_skipped():
    session = ExperimentSessionFactory.create()
    team = session.experiment.team
    openai_model = LlmProviderModelFactory.create(team=team, type="openai", name=get_default_model("openai").name)
    openai = LlmProviderFactory.create(team=team, type="openai", extra_data=UNCHECKED)
    LlmProviderFactory.create(team=team, type="openrouter", extra_data=VERIFIED)

    assert _event_bot(session).get_provider_and_model() == (openai, openai_model)


@pytest.mark.django_db()
def test_a_jev_provider_is_skipped():
    """Jev has a model of its own but cannot write the event's message."""
    session = ExperimentSessionFactory.create()
    team = session.experiment.team
    LlmProviderModelFactory.create(team=team, type="jev", name=get_default_model("jev").name)
    LlmProviderFactory.create(team=team, type="jev", extra_data=VERIFIED)
    openai_model = LlmProviderModelFactory.create(team=team, type="openai", name=get_default_model("openai").name)
    openai = LlmProviderFactory.create(team=team, type="openai", extra_data=UNCHECKED)

    assert _event_bot(session).get_provider_and_model() == (openai, openai_model)


@pytest.mark.django_db()
def test_provider_with_only_a_team_model_uses_that_model():
    session = ExperimentSessionFactory.create()
    team = session.experiment.team
    LlmProviderModelFactory.create(team=team, type="openai")
    LlmProviderFactory.create(team=team, type="openai", extra_data=UNCHECKED)
    openrouter_model = LlmProviderModelFactory.create(team=team, type="openrouter", name="some/model")
    openrouter = LlmProviderFactory.create(team=team, type="openrouter", extra_data=VERIFIED)

    assert _event_bot(session).get_provider_and_model() == (openrouter, openrouter_model)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "provider_types",
    [
        pytest.param([], id="no-providers"),
        pytest.param(["openrouter"], id="no-provider-has-a-chat-model"),
    ],
)
def test_no_provider_and_model(provider_types):
    session = ExperimentSessionFactory.create()
    for provider_type in provider_types:
        LlmProviderFactory.create(team=session.experiment.team, type=provider_type, extra_data=VERIFIED)
    bot = _event_bot(session)

    assert bot.get_provider_and_model() is None
    with pytest.raises(Exception, match="No LLM provider found"):
        bot.get_user_message("Test event prompt")
