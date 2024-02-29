"""
This test suite is designed to ensure that the base channel functionality is working as
intended. It utilizes the Telegram channel subclass to serve as a testing framework.
"""

from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ExperimentChannel
from apps.chat.channels import TelegramChannel
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus, VoiceResponseBehaviours
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.langchain import mock_experiment_llm

from .message_examples import telegram_messages


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_incoming_message_adds_channel_info(_get_llm_response, _send_text_to_user_mock, telegram_channel):
    """When an `experiment_session` is created, channel specific info like `external_chat_id` and
    `experiment_channel` should also be added to the `experiment_session`
    """

    chat_id = 123123
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)

    experiment_session = ExperimentSession.objects.filter(
        experiment=telegram_channel.experiment, external_chat_id=chat_id
    ).first()
    assert experiment_session is not None
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_channel_added_for_experiment_session(_get_llm_response, _send_text_to_user_mock, telegram_channel):
    chat_id = 123123
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)
    experiment_session = ExperimentSession.objects.filter(external_chat_id=chat_id).get()
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_incoming_message_uses_existing_experiment_session(
    _get_llm_response, _send_text_to_user_mock, telegram_channel
):
    """Approach: Simulate messages coming in after one another in order to test this behaviour"""
    chat_id = 12312331
    experiment = telegram_channel.experiment

    # First message
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)

    # Let's find the session it created
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, external_chat_id=chat_id
    ).count()
    assert experiment_sessions_count == 1

    # Let's mock the _create_new_experiment_session so we can verify later that it was not called
    telegram_channel._create_new_experiment_session = Mock()

    # Second message
    _simulate_user_message(telegram_channel, message)

    # Assertions
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, external_chat_id=chat_id
    ).count()
    assert experiment_sessions_count == 1

    telegram_channel._create_new_experiment_session.assert_not_called()


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_different_sessions_created_for_different_users(_get_llm_response, telegram_channel):
    user_1_chat_id = 00000
    user_2_chat_id = 11111

    # First user's message
    user_1_message = telegram_messages.text_message(chat_id=user_1_chat_id)
    _simulate_user_message(telegram_channel, user_1_message)

    # Calling new_user_message added an experiment_session, so we should remove it before reusing the instance
    telegram_channel.experiment_session = None

    # Second user's message
    user_2_message = telegram_messages.text_message(chat_id=user_2_chat_id)
    _simulate_user_message(telegram_channel, user_2_message)

    # Assertions
    experiment_sessions_count = ExperimentSession.objects.count()
    assert experiment_sessions_count == 2

    assert ExperimentSession.objects.filter(external_chat_id=user_1_chat_id).exists()
    assert ExperimentSession.objects.filter(external_chat_id=user_2_chat_id).exists()


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_reset_command_creates_new_experiment_session(_send_text_to_user_mock, telegram_channel):
    """The reset command should create a new session when the user conversed with the bot"""
    telegram_chat_id = 00000
    normal_message = telegram_messages.text_message(chat_id=telegram_chat_id)

    _simulate_user_message(telegram_channel, normal_message)

    reset_message = telegram_messages.text_message(
        chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND
    )
    telegram_channel.new_user_message(reset_message)
    sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).order_by("created_at").all()
    assert len(sessions) == 2
    new_session = sessions[0]
    old_session = sessions[1]
    assert new_session.ended_at is not None
    assert old_session.ended_at is None


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.bots.TopicBot._call_predict", return_value="OK")
@patch("apps.chat.bots.create_conversation")
def test_reset_conversation_does_not_create_new_session(
    create_conversation, _call_predict, _send_text_to_user_mock, telegram_channel
):
    """The reset command should not create a new session when the user haven't conversed with the bot yet"""
    telegram_chat_id = 00000

    message1 = telegram_messages.text_message(chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND)
    _simulate_user_message(telegram_channel, message1)

    message2 = telegram_messages.text_message(chat_id=telegram_chat_id, message_text=ExperimentChannel.RESET_COMMAND)
    _simulate_user_message(telegram_channel, message2)

    sessions = ExperimentSession.objects.filter(external_chat_id=telegram_chat_id).all()
    assert len(sessions) == 1
    # The reset command should not be saved in the history
    assert sessions[0].chat.get_langchain_messages() == []


def _simulate_user_message(channel_instance, user_message: str):
    with mock_experiment_llm(channel_instance.experiment, responses=["OK"]):
        channel_instance.new_user_message(user_message)


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


@pytest.fixture()
@patch("apps.channels.models._set_telegram_webhook")
def telegram_channel(db):
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    experiment.conversational_consent_enabled = False
    channel = ExperimentChannelFactory(experiment=experiment)
    channel = TelegramChannel(experiment_channel=channel)
    channel.telegram_bot = Mock()
    return channel


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("voice_behaviour", "user_message", "voice_response_expected"),
    [
        (VoiceResponseBehaviours.ALWAYS, telegram_messages.text_message(), True),
        (VoiceResponseBehaviours.ALWAYS, telegram_messages.audio_message(), True),
        (VoiceResponseBehaviours.NEVER, telegram_messages.text_message(), False),
        (VoiceResponseBehaviours.NEVER, telegram_messages.audio_message(), False),
        (VoiceResponseBehaviours.RECIPROCAL, telegram_messages.text_message(), False),
        (VoiceResponseBehaviours.RECIPROCAL, telegram_messages.audio_message(), True),
    ],
)
@patch("apps.chat.channels.TelegramChannel._get_voice_transcript")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._reply_voice_message")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_voice_response_behaviour(
    get_llm_response,
    _reply_voice_message,
    send_text_to_user,
    get_voice_transcript,
    voice_behaviour,
    user_message,
    voice_response_expected,
    telegram_channel,
):
    get_voice_transcript.return_value = "Hello bot. Please assist me"
    get_llm_response.return_value = "Hello user. No"
    experiment = telegram_channel.experiment
    experiment.voice_response_behaviour = voice_behaviour
    experiment.save()

    telegram_channel.new_user_message(user_message)

    assert _reply_voice_message.called == voice_response_expected
    assert send_text_to_user.called == (not voice_response_expected)


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel._get_voice_transcript")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._reply_voice_message")
@patch("apps.chat.channels.TelegramChannel._get_llm_response")
def test_reply_with_text_when_synthetic_voice_not_specified(
    get_llm_response,
    _reply_voice_message,
    send_text_to_user,
    get_voice_transcript,
    telegram_channel,
):
    get_voice_transcript.return_value = "Hello bot. Please assist me"
    get_llm_response.return_value = "Hello user. No"
    experiment = telegram_channel.experiment
    experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    # Let's remove the synthetic voice and see what happens
    experiment.synthetic_voice = None
    experiment.save()

    telegram_channel.new_user_message(telegram_messages.text_message())

    _reply_voice_message.assert_not_called()
    send_text_to_user.assert_called()
