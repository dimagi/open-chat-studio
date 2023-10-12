import json

from django.test import TestCase
from mock import Mock, patch
from telebot import types

from apps.channels.models import ExperimentChannel
from apps.chat.message_handlers import RESET_COMMAND, TelegramMessageHandler
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, Prompt
from apps.users.models import CustomUser


class TelegramMessageHandlerTest(TestCase):
    @patch("apps.channels.models._set_telegram_webhook")
    def setUp(self, _set_telegram_webhook):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.user = CustomUser.objects.create_user(username="testuser")
        self.prompt = Prompt.objects.create(
            owner=self.user,
            name="test-prompt",
            description="test",
            prompt="You are a helpful assistant",
        )
        self.experiment = Experiment.objects.create(
            owner=self.user,
            name="TestExperiment",
            description="test",
            chatbot_prompt=self.prompt,
            consent_form=ConsentForm.get_default(),
        )
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel", experiment=self.experiment, extra_data={"bot_token": "123123123"}, platform="telegram"
        )

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.message_handlers.TelegramMessageHandler._get_llm_response")
    def test_incoming_message_adds_adds_channel_info(self, _get_llm_response, _send_text_to_user_mock):
        """When an `experiment_session` is created, channel specific info like `external_chat_id` and
        `experiment_channel` should also be added to the `experiment_session`
        """
        message_handler = self._get_telegram_message_handler(self.experiment_channel)

        message = _telegram_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        experiment_session = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).first()
        self.assertIsNotNone(experiment_session)
        self.assertIsNotNone(experiment_session.experiment_channel)

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.message_handlers.TelegramMessageHandler._get_llm_response")
    def test_channel_added_for_experiment_session(self, _get_llm_response, _send_text_to_user_mock):
        # Let's send two messages. The first one to create the sessions for us and the second one for testing
        # Message 1
        message_handler = self._get_telegram_message_handler(self.experiment_channel)
        message = _telegram_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Let's remove the `experiment_channel` from experiment_session
        experiment_session = ExperimentSession.objects.filter(external_chat_id=self.telegram_chat_id).first()
        experiment_session.experiment_channel = None
        experiment_session.save()

        # Message 2
        message_handler = self._get_telegram_message_handler(self.experiment_channel)
        message = _telegram_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)
        experiment_session = ExperimentSession.objects.filter(external_chat_id=self.telegram_chat_id).first()
        self.assertIsNotNone(experiment_session.experiment_channel)

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.message_handlers.TelegramMessageHandler._get_llm_response")
    def test_incoming_message_uses_existing_experiment_session(self, _get_llm_response, _send_text_to_user_mock):
        """Approach: Simulate messages coming in after each other in order to test this behaviour"""
        # First message
        message_handler = self._get_telegram_message_handler(self.experiment_channel)

        message = _telegram_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Let's find the session it created
        experiment_sessions_count = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).count()
        self.assertEqual(experiment_sessions_count, 1)

        # Second message
        # First we mock the _create_new_experiment_session so we can verify that it was not called
        message_handler._create_new_experiment_session = Mock()

        # Now let's simulate the incoming message
        message = _telegram_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Assertions
        experiment_sessions_count = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).count()
        self.assertEqual(experiment_sessions_count, 1)

        message_handler._create_new_experiment_session.assert_not_called()

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.message_handlers.TelegramMessageHandler._get_llm_response")
    def test_different_sessions_created_for_different_users(self, _get_llm_response, _send_text_to_user_mock):
        # First user's message
        message_handler_1 = self._get_telegram_message_handler(self.experiment_channel)

        message = _telegram_message(chat_id=00000)
        message_handler_1.new_user_message(message)

        # First user's message
        message_handler_2 = self._get_telegram_message_handler(self.experiment_channel)
        message = _telegram_message(chat_id=11111)
        message_handler_2.new_user_message(message)

        # Assertions
        experiment_sessions_count = ExperimentSession.objects.count()
        self.assertEqual(experiment_sessions_count, 2)

        self.assertTrue(ExperimentSession.objects.filter(external_chat_id=00000).exists())
        self.assertTrue(ExperimentSession.objects.filter(external_chat_id=11111).exists())

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.bots.TopicBot._call_predict", return_value="OK")
    @patch("apps.chat.bots.create_conversation")
    def test_reset_command_creates_new_experiment_session(
        self, create_conversation, _call_predict, _send_text_to_user_mock
    ):
        """The reset command should create a new session when the user conversed with the bot"""
        telegram_chat_id = 00000
        message_handler = self._get_telegram_message_handler(self.experiment_channel)
        normal_message = _telegram_message(chat_id=telegram_chat_id)
        message_handler.new_user_message(normal_message)

        message_handler = self._get_telegram_message_handler(self.experiment_channel)
        reset_message = _telegram_message(chat_id=telegram_chat_id, message_text=RESET_COMMAND)
        message_handler.new_user_message(reset_message)
        sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).all()
        self.assertEqual(len(sessions), 2)
        self.assertIsNotNone(sessions[0].ended_at)
        self.assertIsNone(sessions[1].ended_at)

    @patch("apps.chat.message_handlers.TelegramMessageHandler.send_text_to_user")
    @patch("apps.chat.bots.TopicBot._call_predict", return_value="OK")
    @patch("apps.chat.bots.create_conversation")
    def test_reset_conversation_does_not_create_new_session(
        self, create_conversation, _call_predict, _send_text_to_user_mock
    ):
        """The reset command should not create a new session when the user haven't conversed with the bot yet"""
        telegram_chat_id = 00000
        message_handler = self._get_telegram_message_handler(self.experiment_channel)

        message1 = _telegram_message(chat_id=telegram_chat_id, message_text=RESET_COMMAND)
        message_handler.new_user_message(message1)

        message2 = _telegram_message(chat_id=telegram_chat_id, message_text=RESET_COMMAND)
        message_handler.new_user_message(message2)

        sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).all()
        self.assertEqual(len(sessions), 1)
        # The reset command should not be saved in the history
        self.assertEqual(sessions[0].chat.get_langchain_messages(), [])

    def _get_telegram_message_handler(self, experiment_channel: ExperimentChannel) -> TelegramMessageHandler:
        message_handler = TelegramMessageHandler(channel=experiment_channel)
        message_handler.telegram_bot = Mock()
        return message_handler


def _telegram_message(chat_id: int, message_text: str = "Hi there") -> types.Message:
    message_data = {
        "update_id": 432101234,
        "message": {
            "message_id": 576,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Chris",
                "last_name": "Smit",
                "username": "smittiec",
                "language_code": "en",
            },
            "chat": {
                "id": chat_id,
                "first_name": "Chris",
                "last_name": "Smit",
                "username": "smittiec",
                "type": "private",
            },
            "date": 1690376696,
            "text": message_text,
        },
    }
    json_data = json.dumps(message_data)
    return types.Update.de_json(json_data).message
