from datetime import datetime, timedelta

import pytz
from django.test import TestCase
from freezegun import freeze_time
from mock import Mock, patch

from apps.channels.models import ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.tasks import _bot_prompt_for_user, _no_activity_pings
from apps.experiments.models import (
    ConsentForm,
    Experiment,
    ExperimentSession,
    NoActivityMessageConfig,
    Prompt,
    SessionStatus,
)
from apps.experiments.views.experiment import _start_experiment_session
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team
from apps.users.models import CustomUser


class TasksTest(TestCase):
    def setUp(self):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.team = Team.objects.create(name="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.prompt = Prompt.objects.create(
            team=self.team,
            owner=self.user,
            name="test-prompt",
            description="test",
            prompt="You are a helpful assistant",
        )
        self.no_activity_config = NoActivityMessageConfig.objects.create(
            team=self.team, message_for_bot="Some message", name="Some name", max_pings=3, ping_after=1
        )
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            chatbot_prompt=self.prompt,
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
        )
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel", experiment=self.experiment, extra_data={"bot_token": "123123123"}, platform="telegram"
        )
        self.experiment_session = self._add_session(self.experiment)

    @patch("apps.chat.bots.TopicBot._get_response")
    @patch("apps.chat.bots.create_conversation")
    def test_getting_ping_message_saves_history(self, create_conversation, _get_response_mock):
        create_conversation.return_value = Mock()
        expected_ping_message = "Hey, answer me!"
        _get_response_mock.return_value = expected_ping_message
        response = _bot_prompt_for_user(self.experiment_session, "Some message")
        messages = ChatMessage.objects.filter(chat=self.experiment_session.chat).all()
        # Only the AI message should be there
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_type, "ai")
        self.assertEqual(response, expected_ping_message)
        self.assertEqual(messages[0].content, expected_ping_message)

    @patch("apps.chat.tasks._bot_prompt_for_user", return_value="Please answer")
    @patch("apps.chat.tasks._try_send_message")
    def test_no_activity_ping_triggered_for_active_sessions(self, _bot_prompt_for_user, _try_send_message):
        second_experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment2",
            description="test2",
            chatbot_prompt=self.prompt,
            no_activity_config=None,
            consent_form=ConsentForm.get_default(self.team),
        )
        ExperimentChannel.objects.create(
            name="TestChannel2", experiment=second_experiment, extra_data={"bot_token": "222222"}, platform="telegram"
        )
        # Experiment sessions which should be pinged
        experiment_session_should_fire = self.experiment_session
        self._add_chats(experiment_session_should_fire, last_message_type="ai")
        experiment_session_setup = self._add_session(self.experiment, session_status=SessionStatus.SETUP)
        self._add_chats(experiment_session_setup, last_message_type="ai")

        # Experiment sessions for which no ping should trigger
        # See the docstring for `_no_activity_pings` for the criteria of a ping message to be triggered
        # Criteria number 1 not met
        self._add_session(self.experiment, session_status=SessionStatus.ACTIVE)
        # Criteria number 2 not met
        experiment_session_setup = self._add_session(self.experiment, session_status=SessionStatus.PENDING_REVIEW)
        self._add_chats(experiment_session_setup, last_message_type="ai")
        experiment_session_completed = self._add_session(self.experiment, session_status=SessionStatus.COMPLETE)
        self._add_chats(experiment_session_completed, last_message_type="ai")
        experiment_session_completed = self._add_session(self.experiment, session_status=SessionStatus.UNKNOWN)
        self._add_chats(experiment_session_completed, last_message_type="ai")
        # Criteria number 3 not met
        experiment_session_no_config = self._add_session(second_experiment, session_status=SessionStatus.ACTIVE)
        self._add_chats(experiment_session_no_config, last_message_type="ai")
        # Criteria number 4 not met
        experiment_session_not_eligible = self._add_session(self.experiment, session_status=SessionStatus.SETUP)
        self._add_chats(experiment_session_not_eligible, last_message_type=ChatMessageType.HUMAN)

        # frozen_time = "2023-08-21 12:00:00"  # Set the desired frozen time
        with freeze_time(datetime.utcnow() + timedelta(minutes=5)):
            _no_activity_pings()
        self.assertEqual(_try_send_message.call_count, 2)

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
