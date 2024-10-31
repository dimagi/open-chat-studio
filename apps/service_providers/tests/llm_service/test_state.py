import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.custom_actions.models import CustomAction
from apps.service_providers.llm_service.state import AssistantExperimentState, ChatExperimentState
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


# TODO: I need more tests please!
@pytest.mark.django_db()
class TestChatExperimentState:
    @pytest.mark.parametrize("prompt", ["a custom prompt", None])
    def test_get_custom_actions_prompt(self, prompt, session):
        action = CustomAction(
            name="Weather Service",
            description="Get the weather for a specific location",
            prompt=prompt,
            api_schema={
                "openapi": "3.0.0",
                "info": {"title": "Weather API", "version": "1.0.0"},
            },
        )
        state = ChatExperimentState(session=session, experiment=session.experiment)
        actions_prompt = state.get_custom_actions_prompt([action])
        assert action.api_schema_json in actions_prompt
        if prompt:
            assert prompt in actions_prompt
        else:
            assert "Additional Instructions" not in actions_prompt

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
