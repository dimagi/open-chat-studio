import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.service_providers.llm_service.adapters import (
    ChatAdapter,
)
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestChatAdapter:
    def test_save_message_to_history_stores_ai_message_on_state(self, session):
        adapter = ChatAdapter.from_experiment(session=session, experiment=session.experiment)
        adapter.save_message_to_history(message="hi", type_=ChatMessageType.HUMAN)
        assert adapter.ai_message is None
        adapter.save_message_to_history(message="hi human", type_=ChatMessageType.AI)
        assert adapter.ai_message == ChatMessage.objects.get(message_type=ChatMessageType.AI)
