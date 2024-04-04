import pytest
from django.test import TestCase

from apps.channels.models import ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.tasks import bot_prompt_for_user
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, NoActivityMessageConfig, SessionStatus
from apps.experiments.views.experiment import _start_experiment_session
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm


class TasksTest(TestCase):
    def setUp(self):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.team = Team.objects.create(name="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.no_activity_config = NoActivityMessageConfig.objects.create(
            team=self.team, message_for_bot="Some message", name="Some name", max_pings=3, ping_after=1
        )
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            prompt_text="You are a helpful assistant",
            no_activity_config=self.no_activity_config,
            consent_form=ConsentForm.get_default(self.team),
            llm_provider=LlmProvider.objects.create(
                name="test",
                type="openai",
                team=self.team,
                config={
                    "openai_api_key": "123123123",
                },
            ),
            llm="gpt-4",
        )
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel", experiment=self.experiment, extra_data={"bot_token": "123123123"}, platform="telegram"
        )
        self.experiment_session = self._add_session(self.experiment)

    def test_getting_ping_message_saves_history(self):
        expected_ping_message = "Hey, answer me!"
        with mock_experiment_llm(self.experiment, responses=[expected_ping_message]):
            response = bot_prompt_for_user(self.experiment_session, "Some message")
        messages = ChatMessage.objects.filter(chat=self.experiment_session.chat).all()
        # Only the AI message should be there
        assert len(messages) == 1
        assert messages[0].message_type == "ai"
        assert response == expected_ping_message
        assert messages[0].content == expected_ping_message

    def _add_session(self, experiment: Experiment, session_status: SessionStatus = SessionStatus.ACTIVE):
        experiment_session = _start_experiment_session(
            experiment, external_chat_id=self.telegram_chat_id, experiment_channel=self.experiment_channel
        )
        experiment_session.status = session_status
        experiment_session.save()
        return experiment_session

    def _add_chats(self, experiment_session: ExperimentSession, last_message_type: ChatMessageType):
        ChatMessage.objects.create(chat=experiment_session.chat, message_type=ChatMessageType.HUMAN, content="Hi")
        if last_message_type == ChatMessageType.AI:
            ChatMessage.objects.create(
                chat=experiment_session.chat,
                message_type=ChatMessageType.AI,
                content="Hello. How can I assist you today?",
            )


@pytest.mark.django_db()
def test_no_activity_ping_with_assistant_bot():
    session = ExperimentSessionFactory()
    local_assistant = OpenAiAssistantFactory()
    session.experiment.assistant = local_assistant

    expected_ping_message = "Hey, answer me!"
    with mock_experiment_llm(session.experiment, responses=[expected_ping_message]):
        response = bot_prompt_for_user(session, "Some message")
    messages = ChatMessage.objects.filter(chat=session.chat).all()
    # Only the AI message should be there
    assert len(messages) == 1
    assert messages[0].message_type == "ai"
    assert response == expected_ping_message
    assert messages[0].content == expected_ping_message
