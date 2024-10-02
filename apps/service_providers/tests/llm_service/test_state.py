import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.llm_service.state import AssistantExperimentState, ChatExperimentState
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestChatExperimentState:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        state = ChatExperimentState(session=session, experiment=session.experiment)
        state.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert state.ai_message is None
        state.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        assert state.ai_message == ChatMessage.objects.get(message_type=ChatMessageType.AI)


@pytest.mark.django_db()
class TestAssistantExperimentState:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        state = AssistantExperimentState(session=session, experiment=session.experiment)
        state.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert state.ai_message is None
        state.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        assert state.ai_message == ChatMessage.objects.get(message_type=ChatMessageType.AI)
