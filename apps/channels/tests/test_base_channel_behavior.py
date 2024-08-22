"""
This test suite is designed to ensure that the base channel functionality is working as
intended. It utilizes the Telegram channel subclass to serve as a testing framework.
"""

import re
from unittest.mock import Mock, patch

import pytest

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import URL_REGEX, ChannelBase, TelegramChannel, strip_urls_and_emojis
from apps.chat.models import ChatMessageType
from apps.experiments.models import (
    ExperimentRoute,
    ExperimentSession,
    Participant,
    SessionStatus,
    VoiceResponseBehaviours,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.langchain import build_fake_llm_service, mock_experiment_llm

from .message_examples import telegram_messages


@pytest.fixture()
def telegram_channel(db):
    experiment = ExperimentFactory(conversational_consent_enabled=False)
    channel = ExperimentChannelFactory(experiment=experiment)
    channel = TelegramChannel(experiment=experiment, experiment_channel=channel)
    channel.telegram_bot = Mock()
    return channel


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response", Mock())
def test_incoming_message_adds_channel_info(telegram_channel):
    """When an `experiment_session` is created, channel specific info like `identifier` and
    `experiment_channel` should also be added to the `experiment_session`
    """

    chat_id = 123123
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)

    experiment_session = ExperimentSession.objects.filter(
        experiment=telegram_channel.experiment, participant__identifier=chat_id
    ).get()
    assert experiment_session is not None
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response", Mock())
def test_channel_added_for_experiment_session(telegram_channel):
    chat_id = 123123
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)
    participant = Participant.objects.get(identifier=chat_id)
    experiment_session = participant.experimentsession_set.first()
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response", Mock())
def test_incoming_message_uses_existing_experiment_session(telegram_channel):
    """Approach: Simulate messages coming in after one another in order to test this behaviour"""
    chat_id = 12312331
    experiment = telegram_channel.experiment

    # First message
    message = telegram_messages.text_message(chat_id=chat_id)
    _simulate_user_message(telegram_channel, message)

    # Let's find the session it created
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, participant__identifier=chat_id
    ).count()
    assert experiment_sessions_count == 1

    # Let's mock the _create_new_experiment_session so we can verify later that it was not called
    telegram_channel._create_new_experiment_session = Mock()

    # Second message
    _simulate_user_message(telegram_channel, message)

    # Assertions
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, participant__identifier=chat_id
    ).count()
    assert experiment_sessions_count == 1

    telegram_channel._create_new_experiment_session.assert_not_called()


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
def test_different_sessions_created_for_different_users(telegram_channel):
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
    assert ExperimentSession.objects.for_chat_id(user_1_chat_id).exists()
    assert ExperimentSession.objects.for_chat_id(user_2_chat_id).exists()


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
def test_different_participants_created_for_same_user_in_different_teams():
    chat_id = 00000
    user_message = telegram_messages.text_message(chat_id=chat_id)

    experiment1 = ExperimentFactory()
    exp_channel1 = ExperimentChannelFactory(experiment=experiment1)
    channel1 = TelegramChannel(experiment1, exp_channel1)
    channel1.telegram_bot = Mock()

    experiment2 = ExperimentFactory()
    exp_channel2 = ExperimentChannelFactory(experiment=experiment2)
    channel2 = TelegramChannel(experiment2, exp_channel2)
    channel2.telegram_bot = Mock()

    assert experiment1.team != experiment2.team

    _simulate_user_message(channel1, user_message)
    _simulate_user_message(channel2, user_message)

    experiment_sessions_count = ExperimentSession.objects.count()
    assert experiment_sessions_count == 2
    assert Participant.objects.count() == 2
    participant1 = Participant.objects.get(team=experiment1.team, identifier=chat_id)
    participant2 = Participant.objects.get(team=experiment2.team, identifier=chat_id)
    assert participant1 != participant2


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
    sessions = ExperimentSession.objects.for_chat_id(telegram_chat_id).order_by("created_at").all()
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

    sessions = ExperimentSession.objects.for_chat_id(telegram_chat_id).all()
    assert len(sessions) == 1
    # The reset command should not be saved in the history
    assert sessions[0].chat.get_langchain_messages() == []


def _simulate_user_message(channel_instance, user_message: str):
    with mock_experiment_llm(channel_instance.experiment, responses=["OK"]):
        channel_instance.new_user_message(user_message)


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.submit_input_to_llm", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response", Mock())
@patch("apps.chat.channels.TelegramChannel._generate_response_for_user")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_pre_conversation_flow(send_text_to_user_mock, generate_response_for_user):
    """This simulates an interaction between a user and the bot. The user initiated the conversation, so the
    user and bot must first go through the pre conversation flow. The following needs to happen:
    - The user must give consent
    - The user must indicate that they filled out the survey
    """
    bot_response_to_seed_message = "Hi user"
    generate_response_for_user.return_value = bot_response_to_seed_message
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment, ExperimentChannelFactory(experiment=experiment))
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
    generate_response_for_user.assert_called()
    assert send_text_to_user_mock.call_args[0][0] == bot_response_to_seed_message


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
@patch("apps.chat.channels.TopicBot", Mock())
def test_unsupported_message_type_creates_system_message():
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None
    telegram_chat_id = "123"

    channel.new_user_message(telegram_messages.photo_message(telegram_chat_id))
    assert channel.experiment_session is not None

    channel.experiment_session.refresh_from_db()
    message = channel.experiment_session.chat.messages.first()
    assert message.message_type == ChatMessageType.SYSTEM
    assert channel.message.content_type_unparsed == "photo"


@pytest.mark.django_db()
@patch("apps.chat.channels.ChannelBase._unsupported_message_type_response")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_unsupported_message_type_triggers_bot_response(send_text_to_user, _unsupported_message_type_response):
    bot_response = "Nope, not suppoerted laddy"
    _unsupported_message_type_response.return_value = bot_response
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TelegramChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None
    telegram_chat_id = "123"

    channel.new_user_message(telegram_messages.photo_message(telegram_chat_id))
    assert channel.experiment_session is not None
    assert send_text_to_user.call_args[0][0] == bot_response


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
@patch("apps.chat.channels.TelegramChannel._get_bot_response")
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
@pytest.mark.parametrize(
    ("voice_behaviour", "voice_response_expected"),
    [
        (VoiceResponseBehaviours.ALWAYS, True),
        (VoiceResponseBehaviours.NEVER, False),
        (VoiceResponseBehaviours.RECIPROCAL, True),
    ],
)
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._reply_voice_message")
@patch("apps.chat.channels.TelegramChannel._generate_response_for_user")
def test_failed_transcription_informs_the_user(
    _generate_response_for_user,
    _reply_voice_message,
    send_text_to_user,
    voice_behaviour,
    voice_response_expected,
    telegram_channel,
):
    """When we fail to transcribe the user's voice message, we should inform them"""

    _generate_response_for_user.return_value = "Sorry, we could not transcribe your message"
    experiment = telegram_channel.experiment
    experiment.voice_response_behaviour = voice_behaviour
    experiment.save()

    with pytest.raises(Exception, match="Nope"):
        with patch("apps.chat.channels.TelegramChannel._get_voice_transcript", side_effect=Exception("Nope")):
            telegram_channel.new_user_message(telegram_messages.audio_message())

    assert _reply_voice_message.called == voice_response_expected
    assert send_text_to_user.called == (not voice_response_expected)


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel._get_voice_transcript")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel._reply_voice_message")
@patch("apps.chat.channels.TelegramChannel._get_bot_response")
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


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("message_func", "message_type"),
    [(telegram_messages.audio_message, "voice"), (telegram_messages.text_message, "text")],
)
@patch("apps.chat.channels.TelegramChannel.send_text_to_user", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response", Mock())
@patch("apps.chat.channels.TelegramChannel._add_message_to_history", Mock())
def test_user_query_extracted_for_pre_conversation_flow(message_func, message_type):
    """The user query need to be available during the pre-conversation flow. Simply looking at `message_text` for
    this is erroneous, since it will not be available when the user sends a voice message.

    This test simply makes sure that we are able to get the user query when we need it.
    """
    experiment = ExperimentFactory(conversational_consent_enabled=True, seed_message="Hi human")
    experiment_session = ExperimentSessionFactory(experiment=experiment)

    channel = TelegramChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    channel.experiment_session = experiment_session
    pre_survey = experiment.pre_survey
    telegram_chat_id = "123"
    assert pre_survey

    with (
        patch("apps.chat.channels.TelegramChannel._get_voice_transcript") as _get_voice_transcript,
        patch("apps.chat.channels.TelegramChannel._inform_user_of_error") as _inform_user_of_error,
    ):
        _get_voice_transcript.return_value = "Hi botty"

        message = message_func(chat_id=telegram_chat_id)
        channel.new_user_message(message)
        if message_type == "voice":
            _get_voice_transcript.assert_called()
        elif message_type == "text":
            _get_voice_transcript.assert_not_called()

        _inform_user_of_error.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize("platform", [platform for platform, _ in ChannelPlatform.choices])
def test_all_channels_can_be_instantiated_from_a_session(platform, twilio_provider):
    """This test checks all channel types and makes sure that we can instantiate each one by calling
    `ChannelBase.from_experiment_session`. For the sake of ease, we assume all platforms uses the Twilio
    messenging provider.
    """
    experiment = ExperimentFactory()
    experiment_channel = ExperimentChannelFactory(
        messaging_provider=twilio_provider, experiment=experiment, platform=platform
    )
    session = ExperimentSessionFactory(experiment_channel=experiment_channel)
    channel = ChannelBase.from_experiment_session(session)
    assert type(channel) in ChannelBase.__subclasses__()


@pytest.mark.django_db()
def test_missing_channel_raises_error(twilio_provider):
    experiment = ExperimentFactory()
    experiment_channel = ExperimentChannelFactory(
        messaging_provider=twilio_provider, experiment=experiment, platform="whatsapp"
    )
    session = ExperimentSessionFactory(experiment_channel=experiment_channel)
    session.experiment_channel.platform = "snail_mail"
    with pytest.raises(Exception, match="Unsupported platform type snail_mail"):
        ChannelBase.from_experiment_session(session)


@pytest.mark.django_db()
@patch("apps.chat.channels.TelegramChannel.send_message_to_user", Mock())
@patch("apps.chat.channels.TelegramChannel._get_bot_response")
def test_participant_reused_across_experiments(_get_bot_response):
    """A single participant should be linked to multiple sessions per team"""
    _get_bot_response.return_value = "Hi human"
    chat_id = 123

    # User chats to experiment 1
    experiment1 = ExperimentFactory()
    team1 = experiment1.team
    tele_channel1 = TelegramChannel(experiment1, ExperimentChannelFactory(experiment=experiment1))
    tele_channel1.telegram_bot = Mock()
    tele_channel1.new_user_message(telegram_messages.text_message(chat_id=chat_id))

    # User chats to experiment 2 that is in the same team
    experiment2 = ExperimentFactory(team=team1)
    tele_channel2 = TelegramChannel(experiment2, ExperimentChannelFactory(experiment=experiment2))
    tele_channel2.telegram_bot = Mock()
    tele_channel2.new_user_message(telegram_messages.text_message(chat_id=chat_id))

    # User chats to experiment 3 that is in a different team
    experiment3 = ExperimentFactory()
    team2 = experiment3.team
    tele_channel3 = TelegramChannel(experiment3, ExperimentChannelFactory(experiment=experiment3))
    tele_channel3.telegram_bot = Mock()
    tele_channel3.new_user_message(telegram_messages.text_message(chat_id=chat_id))

    # There should be 1 participant with identifier = chat_id per team
    assert Participant.objects.filter(team=team1, identifier=chat_id).count() == 1
    assert Participant.objects.filter(team=team2, identifier=chat_id).count() == 1
    # but 2 participants accross all teams with identifier = chat_id
    assert Participant.objects.filter(identifier=chat_id).count() == 2


@pytest.mark.django_db()
def test_strip_urls_and_emojis():
    """
    Test that unique urls are extracted and emojis are stripped out
    """
    text = (
        "Hey there! 😊 Check out this amazing website: https://www.example.com! Also, don't forget to visit"
        " http://www.another-site.org. If you're a fan of coding, you'll love"
        " https://developer.mozilla.org/some/path. Have you seen this awesome cat video? 🐱🐾 Watch it at"
        " [https://www.catvideos.com](https://www.catvideos.com). Let's stay connected on social media: Twitter"
        " (https://twitter.com) and Facebook (https://facebook.com?page=page1). Can't wait to see you there! 🎉✨"
    )
    expected_text = (
        "Hey there!  Check out this amazing website: ! Also, don't forget to visit . If you're a fan of coding, "
        "you'll love . Have you seen this awesome cat video?  Watch it at [](). Let's stay connected on social "
        "media: Twitter () and Facebook (). Can't wait to see you there! "
    )

    output, urls = strip_urls_and_emojis(text)
    assert len(urls) == 6
    assert output == expected_text
    assert "https://www.example.com" in urls
    assert "http://www.another-site.org" in urls
    assert "https://developer.mozilla.org/some/path" in urls
    assert "https://twitter.com" in urls
    assert "https://www.catvideos.com" in urls
    assert "https://facebook.com?page=page1" in urls


def test_url_regex():
    url_pattern = re.compile(URL_REGEX)
    expected_matches = [
        "http://www.example.com",
        "http://www.example.co.za",
        "http://www.example.com/",
        "http://www.example.com?key1=val1&key2=val2",
        "http://www.example.com/some/path?key1=val1&key2=val2",
        "http://example.com",
        "http://example.co.za",
        "http://example.com/",
        "http://example.com?key1=val1&key2=val2",
        "http://example.com/some/path?key1=val1&key2=val2",
        "https://www.example.com",
        "https://www.example.co.za",
        "https://www.example.com/",
        "https://www.example.com?key1=val1&key2=val2",
        "https://www.example.com/some/path?key1=val1&key2=val2",
        "https://example.com",
        "https://example.co.za",
        "https://example.com/",
        "https://example.com?key1=val1&key2=val2",
        "https://example.com/some/path?key1=val1&key2=val2",
    ]

    no_matches = [
        "https//example.com",
        "htrps//example.com",
        "htrps\\example.com",
        "http://example.",
        "http://example!",
        "http://example?",
    ]
    matches = url_pattern.findall("\n".join(expected_matches))

    assert len(matches) == 20

    for url in expected_matches:
        assert url in matches

    for url in no_matches:
        assert url not in matches


@pytest.mark.django_db()
@patch("apps.service_providers.models.VoiceProvider.get_speech_service")
@patch("apps.chat.channels.TelegramChannel._get_voice_transcript")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.chat.channels.TelegramChannel.send_voice_to_user")
@patch("apps.chat.channels.TelegramChannel._get_bot_response")
def test_voice_response_with_urls(
    get_llm_response,
    send_voice_to_user,
    send_text_to_user,
    get_voice_transcript,
    get_speech_service,
    telegram_channel,
):
    get_voice_transcript.return_value = "Hello bot. Give me a URL"
    get_llm_response.return_value = (
        "Here are two urls for you: [this](http://example.co.za?key1=1&key2=2) and [https://some.com](https://some.com)"
    )
    experiment = telegram_channel.experiment
    experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    experiment.save()

    telegram_channel.new_user_message(telegram_messages.text_message())

    assert send_voice_to_user.called is True

    text_message = send_text_to_user.mock_calls[0].args[0]
    assert "http://example.co.za?key1=1&key2=2" in text_message
    assert "https://some.com" in text_message


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("use_processor_bot_voice", "child_bot_has_voice", "router_bot_has_voice", "expected_voice"),
    [
        (True, True, True, "child_bot"),
        (True, False, True, "router_bot"),
        (False, None, True, "router_bot"),
        (False, None, False, None),
    ],
)
@patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
@patch("apps.chat.channels.TelegramChannel.send_voice_to_user", Mock())
@patch("apps.channels.models.ExperimentChannel.webhook_url", Mock())
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_processor_bot_voice_setting(
    send_text_to_user,
    synthesize_voice,
    use_processor_bot_voice,
    child_bot_has_voice,
    router_bot_has_voice,
    expected_voice,
    telegram_channel,
):
    session = ExperimentSessionFactory()
    team = session.team
    experiments = ExperimentFactory.create_batch(2, team=team)
    router_exp, child_exp = experiments

    ExperimentChannelFactory(experiment=router_exp)
    ExperimentRoute.objects.create(team=team, parent=router_exp, child=child_exp, keyword="keyword1", is_default=True)

    router_exp.use_processor_bot_voice = use_processor_bot_voice
    router_exp.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    router_exp.save()
    session.experiment = router_exp
    session.save()

    if not child_bot_has_voice:
        child_exp.voice_provider = child_exp.synthetic_voice = None
        child_exp.save()

    if not router_bot_has_voice:
        router_exp.voice_provider = router_exp.synthetic_voice = None
        router_exp.save()

    fake_service = build_fake_llm_service(responses=["keyword1", "How can I help today?"], token_counts=[0])

    with patch("apps.experiments.models.Experiment.get_llm_service", new=lambda x: fake_service):
        telegram_channel = TelegramChannel.from_experiment_session(session)
        telegram_channel.telegram_bot = Mock()
        telegram_channel.new_user_message(telegram_messages.text_message("Hi"))

    assert telegram_channel.bot.processor_experiment == child_exp

    if router_bot_has_voice:
        synthesize_voice_args = synthesize_voice.call_args_list[0].args
        if expected_voice == "child_bot":
            expected_synthetic_voice = child_exp.synthetic_voice
        elif expected_voice == "router_bot":
            expected_synthetic_voice = router_exp.synthetic_voice

        assert synthesize_voice_args[1] == expected_synthetic_voice
    else:
        send_text_to_user.assert_called()
        synthesize_voice.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("expected_message_type", "response_behaviour", "use_processor_bot_voice"),
    [
        ("text", VoiceResponseBehaviours.NEVER, True),
        ("text", VoiceResponseBehaviours.RECIPROCAL, True),
        ("voice", VoiceResponseBehaviours.ALWAYS, True),
        ("text", VoiceResponseBehaviours.NEVER, False),
        ("text", VoiceResponseBehaviours.RECIPROCAL, False),
        ("voice", VoiceResponseBehaviours.ALWAYS, False),
    ],
)
@patch("apps.chat.channels.TelegramChannel.send_voice_to_user")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.service_providers.speech_service.SpeechService.synthesize_voice", Mock())
def test_send_message_to_user_with_single_bot(
    send_text_to_user, send_voice_to_user, expected_message_type, response_behaviour, use_processor_bot_voice
):
    """A simple test to make sure that when we call `channel_instance.send_message_to_user`, the correct message format
    will be used
    """

    session = ExperimentSessionFactory(
        experiment__use_processor_bot_voice=use_processor_bot_voice,
        experiment__voice_response_behaviour=response_behaviour,
    )
    session.experiment_channel = ExperimentChannelFactory(experiment=session.experiment)

    channel = TelegramChannel.from_experiment_session(experiment_session=session)
    channel.telegram_bot = Mock()

    bot_message = "Hi user"

    channel.send_message_to_user(bot_message)

    if expected_message_type == "text":
        send_text_to_user.assert_called()
        assert send_text_to_user.call_args[0][0] == bot_message
    else:
        send_voice_to_user.assert_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("expected_message_type", "response_behaviour", "use_processor_bot_voice"),
    [
        ("text", VoiceResponseBehaviours.NEVER, True),
        ("text", VoiceResponseBehaviours.RECIPROCAL, True),
        ("voice", VoiceResponseBehaviours.ALWAYS, True),
        ("text", VoiceResponseBehaviours.NEVER, False),
        ("text", VoiceResponseBehaviours.RECIPROCAL, False),
        ("voice", VoiceResponseBehaviours.ALWAYS, False),
    ],
)
@patch("apps.chat.channels.TelegramChannel.send_voice_to_user")
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
@patch("apps.service_providers.speech_service.SpeechService.synthesize_voice", Mock())
def test_send_message_to_user_with_multibot(
    send_text_to_user, send_voice_to_user, expected_message_type, response_behaviour, use_processor_bot_voice
):
    session = ExperimentSessionFactory()
    team = session.team
    experiments = ExperimentFactory.create_batch(2, team=team)
    router_exp, child_exp = experiments
    router_exp.use_processor_bot_voice = use_processor_bot_voice
    router_exp.voice_response_behaviour = response_behaviour
    session.experiment = router_exp
    ExperimentChannelFactory(experiment=router_exp)
    ExperimentRoute.objects.create(team=team, parent=router_exp, child=child_exp, keyword="keyword1", is_default=True)

    channel = TelegramChannel.from_experiment_session(experiment_session=session)
    channel.telegram_bot = Mock()

    bot_message = "Hi user"
    channel.send_message_to_user(bot_message)

    if expected_message_type == "text":
        send_text_to_user.assert_called()
    else:
        send_voice_to_user.assert_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("whitelist", "is_external_user", "identifier", "is_allowed"),
    [
        (["11111"], True, "11111", True),
        ([], True, "11111", True),
        (["11111"], True, "22222", False),
        (["11111"], False, "someone@test.com", True),
    ],
)
@patch("apps.chat.channels.TelegramChannel.send_text_to_user")
def test_participant_authorization(
    send_text_to_user, whitelist, is_external_user, identifier, is_allowed, telegram_channel
):
    message = telegram_messages.text_message(chat_id=identifier)
    experiment = telegram_channel.experiment
    if not is_external_user:
        MembershipFactory(team=experiment.team, user__email=identifier)

    experiment.participant_allowlist = whitelist
    telegram_channel.message = message
    assert telegram_channel._participant_is_allowed() == is_allowed

    if not is_allowed:
        telegram_channel.new_user_message(message)
        send_text_to_user.assert_called()
        assert send_text_to_user.call_args[0][0] == "Sorry, you are not allowed to chat to this bot"
