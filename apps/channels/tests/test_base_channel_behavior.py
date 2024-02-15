"""
This test suite is designed to ensure that the base channel functionality is working as
intended. It utilizes the Telegram channel subclass to serve as a testing framework.
"""

from unittest.mock import Mock, patch

from django.test import TestCase

from apps.channels.models import ExperimentChannel
from apps.chat.channels import TelegramChannel
from apps.chat.models import ChatMessageType
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, SessionStatus
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.langchain import mock_experiment_llm

from .message_examples import telegram_messages


class TelegramMessageHandlerTest(TestCase):
    @patch("apps.channels.models._set_telegram_webhook")
    def setUp(self, _set_telegram_webhook):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.team = Team.objects.create(name="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            prompt_text="You are a helpful assistant",
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

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    @patch("apps.chat.channels.TelegramChannel._get_llm_response")
    def test_incoming_message_adds_channel_info(self, _get_llm_response, _send_text_to_user_mock):
        """When an `experiment_session` is created, channel specific info like `external_chat_id` and
        `experiment_channel` should also be added to the `experiment_session`
        """
        message_handler = self._get_telegram_channel(self.experiment_channel)

        message = telegram_messages.text_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        experiment_session = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).first()
        assert experiment_session is not None
        assert experiment_session.experiment_channel is not None

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    @patch("apps.chat.channels.TelegramChannel._get_llm_response")
    def test_channel_added_for_experiment_session(self, _get_llm_response, _send_text_to_user_mock):
        # Let's send two messages. The first one to create the sessions for us and the second one for testing
        # Message 1
        message_handler = self._get_telegram_channel(self.experiment_channel)
        message = telegram_messages.text_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Let's remove the `experiment_channel` from experiment_session
        experiment_session = ExperimentSession.objects.filter(external_chat_id=self.telegram_chat_id).first()
        experiment_session.experiment_channel = None
        experiment_session.save()

        # Message 2
        message_handler = self._get_telegram_channel(self.experiment_channel)
        message = telegram_messages.text_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)
        experiment_session = ExperimentSession.objects.filter(external_chat_id=self.telegram_chat_id).first()
        assert experiment_session.experiment_channel is not None

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    @patch("apps.chat.channels.TelegramChannel._get_llm_response")
    def test_incoming_message_uses_existing_experiment_session(self, _get_llm_response, _send_text_to_user_mock):
        """Approach: Simulate messages coming in after each other in order to test this behaviour"""
        # First message
        message_handler = self._get_telegram_channel(self.experiment_channel)

        message = telegram_messages.text_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Let's find the session it created
        experiment_sessions_count = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).count()
        assert experiment_sessions_count == 1

        # Second message
        # First we mock the _create_new_experiment_session so we can verify that it was not called
        message_handler._create_new_experiment_session = Mock()

        # Now let's simulate the incoming message
        message = telegram_messages.text_message(chat_id=self.telegram_chat_id)
        message_handler.new_user_message(message)

        # Assertions
        experiment_sessions_count = ExperimentSession.objects.filter(
            experiment=self.experiment, external_chat_id=self.telegram_chat_id
        ).count()
        assert experiment_sessions_count == 1

        message_handler._create_new_experiment_session.assert_not_called()

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    @patch("apps.chat.channels.TelegramChannel._get_llm_response")
    def test_different_sessions_created_for_different_users(self, _get_llm_response, _send_text_to_user_mock):
        # First user's message
        message_handler_1 = self._get_telegram_channel(self.experiment_channel)

        message = telegram_messages.text_message(chat_id=00000)
        message_handler_1.new_user_message(message)

        # First user's message
        message_handler_2 = self._get_telegram_channel(self.experiment_channel)
        message = telegram_messages.text_message(chat_id=11111)
        message_handler_2.new_user_message(message)

        # Assertions
        experiment_sessions_count = ExperimentSession.objects.count()
        assert experiment_sessions_count == 2

        assert ExperimentSession.objects.filter(external_chat_id=0).exists()
        assert ExperimentSession.objects.filter(external_chat_id=11111).exists()

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    def test_reset_command_creates_new_experiment_session(self, _send_text_to_user_mock):
        """The reset command should create a new session when the user conversed with the bot"""
        telegram_chat_id = 00000
        message_handler = self._get_telegram_channel(self.experiment_channel)
        normal_message = telegram_messages.text_message(chat_id=telegram_chat_id)
        with mock_experiment_llm(self.experiment, responses=["OK"]):
            message_handler.new_user_message(normal_message)

        message_handler = self._get_telegram_channel(self.experiment_channel)
        reset_message = telegram_messages.text_message(
            chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND
        )
        message_handler.new_user_message(reset_message)
        sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).all()
        assert len(sessions) == 2
        assert sessions[0].ended_at is not None
        assert sessions[1].ended_at is None

    @patch("apps.chat.channels.TelegramChannel.send_text_to_user")
    @patch("apps.chat.bots.TopicBot._call_predict", return_value="OK")
    @patch("apps.chat.bots.create_conversation")
    def test_reset_conversation_does_not_create_new_session(
        self, create_conversation, _call_predict, _send_text_to_user_mock
    ):
        """The reset command should not create a new session when the user haven't conversed with the bot yet"""
        telegram_chat_id = 00000
        message_handler = self._get_telegram_channel(self.experiment_channel)

        message1 = telegram_messages.text_message(
            chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND
        )
        message_handler.new_user_message(message1)

        message2 = telegram_messages.text_message(
            chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND
        )
        message_handler.new_user_message(message2)

        sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).all()
        assert len(sessions) == 1
        # The reset command should not be saved in the history
        assert sessions[0].chat.get_langchain_messages() == []

    def _get_telegram_channel(self, experiment_channel: ExperimentChannel) -> TelegramChannel:
        message_handler = TelegramChannel(experiment_channel=experiment_channel)
        message_handler.telegram_bot = Mock()
        return message_handler


@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_pre_conversation_flow(_get_llm_response, send_text_to_user_mock, db):
    """This simulates an interaction between a user and the bot. The user initiated the conversation, so the
    user and bot must first go through the pre concersation flow. The following needs to happen:
    - The user must give consent
    - The user must indicate that they filled out the survey
    """
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment_channel=ExperimentChannelFactory(experiment=experiment))
    pre_survey = experiment.pre_survey
    assert pre_survey

    def _user_message(message: str):
        message = telegram_messages.text_message(chat_id=telegram_chat_id, message_text=message)
        channel.new_user_message(message)

    experiment = channel.experiment
    experiment.seed_message = "Hi human"
    experiment.save()
    telegram_chat_id = "123"

    _user_message("Hi")
    chat = channel.experiment_session.chat
    pre_survey_link = channel.experiment_session.get_pre_survey_link()
    confirmation_text = pre_survey.confirmation_text
    expected_survey_text = confirmation_text.format(survey_link=pre_survey_link)
    # Let's see if the bot asked consent
    assert experiment.consent_form.consent_text in chat.messages.last().content
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING
    # It did, now the user gives consent
    _user_message("1")
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY
    # Let's make sure the bot presented the user with the survey
    assert expected_survey_text in chat.messages.last().content
    # Now the user tries to talk
    _user_message("Hi there")
    # Check the status. It should not have changed
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY
    # The bot should be persistent about that survey. Let's make sure it sends it
    assert expected_survey_text in chat.messages.last().content
    # The user caves, and says they did fill it out
    _user_message("1")
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.ACTIVE


@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TopicBot")
@patch("apps.channels.models._set_telegram_webhook")
def test_unsupported_message_type_creates_system_message(_set_telegram_webhook, topic_bot, send_text_to_user, db):
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment_channel=ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None
    telegram_chat_id = "123"

    channel.new_user_message(telegram_messages.photo_message(telegram_chat_id))
    assert channel.experiment_session is not None

    channel.experiment_session.refresh_from_db()
    message = channel.experiment_session.chat.messages.first()
    assert message.message_type == ChatMessageType.SYSTEM
    assert channel.message.content_type_unparsed == "photo"


@patch("apps.chat.channels.ChannelBase._unsupported_message_type_response")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.channels.models._set_telegram_webhook")
def test_unsupported_message_type_triggers_bot_response(
    _set_telegram_webhook, send_text_to_user, _unsupported_message_type_response, db
):
    bot_response = "Nope, not suppoerted laddy"
    _unsupported_message_type_response.return_value = bot_response
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment_channel=ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None
    telegram_chat_id = "123"

    channel.new_user_message(telegram_messages.photo_message(telegram_chat_id))
    assert channel.experiment_session is not None
    assert send_text_to_user.call_args[0][0] == bot_response
