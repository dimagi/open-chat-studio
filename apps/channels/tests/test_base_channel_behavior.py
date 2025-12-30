"""
This test suite is designed to ensure that the base channel functionality is working as
intended.
"""

import re
import uuid
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from apps.annotations.models import TagCategories
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import (
    DEFAULT_ERROR_RESPONSE_TEXT,
    MESSAGE_TYPES,
    URL_REGEX,
    ChannelBase,
    strip_urls_and_emojis,
)
from apps.chat.exceptions import VersionedExperimentSessionsNotAllowedException
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import (
    ExperimentSession,
    Participant,
    ParticipantData,
    SessionStatus,
    VoiceResponseBehaviours,
)
from apps.files.models import File
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.langchain import mock_llm

from ...utils.factories.service_provider_factories import LlmProviderFactory
from ..datamodels import BaseMessage
from .message_examples import base_messages


class TestChannel(ChannelBase):
    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_sent = []
        self.voice_sent = []

    def send_text_to_user(self, text: str):
        self.text_sent.append(text)

    def send_voice_to_user(self, synthetic_voice):
        self.voice_sent.append(synthetic_voice)


@pytest.fixture()
def test_channel(db):
    experiment = ExperimentFactory(conversational_consent_enabled=False)
    channel = ExperimentChannelFactory(experiment=experiment)
    channel = TestChannel(experiment=experiment, experiment_channel=channel)
    return channel


def chat_message_mock():
    chat_message_mock = Mock()
    chat_message_mock.get_attached_files.return_value = []
    return chat_message_mock


@pytest.mark.django_db()
def test_incoming_message_adds_channel_info(test_channel):
    """When an `experiment_session` is created, channel specific info like `identifier` and
    `experiment_channel` should also be added to the `experiment_session`
    """

    chat_id = "123123"
    message = base_messages.text_message(participant_id=chat_id)
    _send_user_message_on_channel(test_channel, message)

    experiment_session = ExperimentSession.objects.filter(
        experiment=test_channel.experiment, participant__identifier=chat_id
    ).get()
    assert experiment_session is not None
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
def test_incoming_message_adds_version_on_session(test_channel):
    """When a `message` is sent, the experiment version should be added saved on `experiment_session`"""
    chat_id = "123123"
    experiment = ExperimentFactory()

    # Creating the v1 and send msg on experiment
    exp_v1 = experiment.create_new_version(is_copy=False)
    test_channel.experiment = exp_v1
    message = base_messages.text_message(participant_id=chat_id)
    _send_user_message_on_channel(test_channel, message)
    assert test_channel.experiment_session.experiment_versions == [1]

    # Creating the v2 and send msg on experiment
    exp_v2 = experiment.create_new_version()
    test_channel.experiment = exp_v2
    message = base_messages.text_message(participant_id=chat_id)
    _send_user_message_on_channel(test_channel, message)
    assert test_channel.experiment_session.experiment_versions == [1, 2]


@pytest.mark.django_db()
def test_channel_added_for_experiment_session(test_channel):
    """Ensure that the experiment session gets a link to the experimentt channel that this is using"""
    chat_id = "123123"
    message = base_messages.text_message(participant_id=chat_id)
    _send_user_message_on_channel(test_channel, message)
    participant = Participant.objects.get(identifier=chat_id)
    experiment_session = participant.experimentsession_set.first()
    assert experiment_session.experiment_channel is not None


@pytest.mark.django_db()
def test_incoming_message_uses_existing_experiment_session(test_channel):
    """Approach: Simulate messages coming in after one another in order to test this behaviour"""
    chat_id = "12312331"
    experiment = test_channel.experiment

    # First message
    message = base_messages.text_message(participant_id=chat_id)
    _send_user_message_on_channel(test_channel, message)

    # Let's find the session it created
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, participant__identifier=chat_id
    ).count()
    assert experiment_sessions_count == 1

    # Let's mock the _create_new_experiment_session so we can verify later that it was not called
    test_channel._create_new_experiment_session = Mock()

    # Second message
    _send_user_message_on_channel(test_channel, message)

    # Assertions
    experiment_sessions_count = ExperimentSession.objects.filter(
        experiment=experiment, participant__identifier=chat_id
    ).count()
    assert experiment_sessions_count == 1

    test_channel._create_new_experiment_session.assert_not_called()


@pytest.mark.django_db()
def test_non_active_sessions_are_not_resused(test_channel):
    """
    Sessions that were ended should not be reused when the user sends a new message. Rather, a new session should be
    created
    """
    participant_id = "12312331"
    experiment = test_channel.experiment

    message = base_messages.text_message(participant_id=participant_id)
    _send_user_message_on_channel(test_channel, message)
    # End the session. This could have been done using a timeout trigger for instance
    test_channel.experiment_session.end()

    # Remove the session from test_channel to simulate a new instance
    test_channel.experiment_session = None

    # When the user sends another message, a new session should be created
    message = base_messages.text_message(participant_id=participant_id)
    _send_user_message_on_channel(test_channel, message)
    assert experiment.sessions.filter(participant__identifier=participant_id, status=SessionStatus.ACTIVE).count() == 1
    assert (
        experiment.sessions.filter(participant__identifier=participant_id, status=SessionStatus.PENDING_REVIEW).count()
        == 1
    )


@pytest.mark.django_db()
def test_different_sessions_created_for_different_users(test_channel):
    user_1_chat_id = "00000"
    user_2_chat_id = "11111"

    # First user's message
    user_1_message = base_messages.text_message(participant_id=user_1_chat_id)
    _send_user_message_on_channel(test_channel, user_1_message)

    # Calling new_user_message added an experiment_session, so we should remove it before reusing the instance
    test_channel.experiment_session = None
    test_channel._participant_identifier = None

    # Second user's message
    user_2_message = base_messages.text_message(participant_id=user_2_chat_id)
    _send_user_message_on_channel(test_channel, user_2_message)

    # Assertions
    experiment_sessions_count = ExperimentSession.objects.count()
    assert experiment_sessions_count == 2
    assert ExperimentSession.objects.filter(participant__identifier=user_1_chat_id).exists()
    assert ExperimentSession.objects.filter(participant__identifier=user_2_chat_id).exists()


@pytest.mark.django_db()
def test_different_participants_created_for_same_user_in_different_teams():
    chat_id = "00000"
    user_message = base_messages.text_message(participant_id=chat_id)

    experiment1 = ExperimentFactory()
    exp_channel1 = ExperimentChannelFactory(experiment=experiment1)
    channel1 = TestChannel(experiment1, exp_channel1)

    experiment2 = ExperimentFactory()
    exp_channel2 = ExperimentChannelFactory(experiment=experiment2)
    channel2 = TestChannel(experiment2, exp_channel2)

    assert experiment1.team != experiment2.team

    _send_user_message_on_channel(channel1, user_message)
    _send_user_message_on_channel(channel2, user_message)

    experiment_sessions_count = ExperimentSession.objects.count()
    assert experiment_sessions_count == 2
    participant1 = Participant.objects.get(team=experiment1.team, identifier=chat_id)
    participant2 = Participant.objects.get(team=experiment2.team, identifier=chat_id)
    assert participant1 != participant2


@pytest.mark.django_db()
@pytest.mark.parametrize("user_input", ["/reset", "/Reset", "/RESET", " /reset "])
def test_reset_command_creates_new_experiment_session(user_input, test_channel):
    """The reset command should create a new session when the user conversed with the bot"""
    participant_id = str(uuid.uuid4())
    normal_message = base_messages.text_message(participant_id=participant_id)

    _send_user_message_on_channel(test_channel, normal_message)

    # mock engagement
    test_channel.experiment_session.user_already_engaged = Mock()

    reset_message = base_messages.text_message(participant_id=participant_id, message_text=user_input)
    response = test_channel.new_user_message(reset_message)
    assert response.content == "Conversation reset"
    sessions = ExperimentSession.objects.filter(participant__identifier=participant_id).order_by("created_at").all()
    assert len(sessions) == 2
    new_session = sessions[0]
    old_session = sessions[1]
    assert new_session.ended_at is not None
    assert old_session.ended_at is None


@pytest.mark.django_db()
def test_reset_conversation_does_not_create_new_session(test_channel):
    """The reset command should not create a new session when the user haven't conversed with the bot yet"""
    participant_id = "123"

    message1 = base_messages.text_message(participant_id=participant_id, message_text=ExperimentChannel.RESET_COMMAND)
    _send_user_message_on_channel(test_channel, message1)

    message2 = base_messages.text_message(participant_id=participant_id, message_text=ExperimentChannel.RESET_COMMAND)
    _send_user_message_on_channel(test_channel, message2)

    sessions = ExperimentSession.objects.filter(participant__identifier=participant_id).all()
    assert len(sessions) == 1
    # The reset command should not be saved in the history
    assert sessions[0].chat.get_langchain_messages() == []


def _send_user_message_on_channel(channel_instance, user_message: BaseMessage):
    with patch("apps.chat.channels.ChannelBase._get_bot_response", return_value=[ChatMessage(content="OK"), None]):
        channel_instance.new_user_message(user_message)


@pytest.mark.django_db()
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._send_seed_message")
def test_pre_conversation_flow(_send_seed_message):
    """This simulates an interaction between a user and the bot. The user initiated the conversation, so the
    user and bot must first go through the pre conversation flow. The following needs to happen:
    - The user must give consent
    - The user must indicate that they filled out the survey
    """
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TestChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    pre_survey = experiment.pre_survey
    assert pre_survey

    def _user_message(message: str):
        message = base_messages.text_message(message_text=message)
        return channel.new_user_message(message)

    experiment = channel.experiment
    experiment.seed_message = "Hi human"
    experiment.save()

    response = _user_message("Hi")
    assert experiment.consent_form.consent_text in response.content
    chat = channel.experiment_session.chat
    pre_survey_link = channel.experiment_session.get_pre_survey_link(experiment)
    confirmation_text = pre_survey.confirmation_text
    expected_survey_text = confirmation_text.format(survey_link=pre_survey_link)
    # Let's see if the bot asked consent
    assert experiment.consent_form.consent_text in chat.messages.last().content
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING
    # It did, now the user gives consent
    response = _user_message("1")
    assert expected_survey_text in response.content
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY
    # Let's make sure the bot presented the user with the survey
    assert expected_survey_text in chat.messages.last().content
    # Now the user tries to talk
    response = _user_message("Hi there")
    assert expected_survey_text in response.content
    # Check the status. It should not have changed
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY
    # The bot should be persistent about that survey. Let's make sure it sends it
    assert expected_survey_text in chat.messages.last().content

    # The user caves, and says they did fill it out
    assert _send_seed_message.call_count == 0
    _send_seed_message.return_value = "Hi human"
    response = _user_message("1")
    assert response.content == "Hi human"
    # Check the status
    channel.experiment_session.refresh_from_db()
    assert channel.experiment_session.status == SessionStatus.ACTIVE
    _send_seed_message.assert_called()


@pytest.mark.django_db()
def test_unsupported_message_type_creates_ai_message():
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    LlmProviderFactory(team=experiment.team)  # needed for EventBot
    channel = TestChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None
    with mock_llm(["error"]):
        channel.new_user_message(base_messages.unsupported_content_type_message())
    assert channel.experiment_session is not None

    channel.experiment_session.refresh_from_db()
    message = channel.experiment_session.chat.messages.first()
    assert message.message_type == ChatMessageType.AI


@pytest.mark.django_db()
@patch("apps.chat.channels.ChannelBase._unsupported_message_type_response")
def test_unsupported_message_type_triggers_bot_response(_unsupported_message_type_response):
    bot_response = "Nope, not supported laddy"
    _unsupported_message_type_response.return_value = bot_response
    experiment = ExperimentFactory(conversational_consent_enabled=True)
    channel = TestChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    assert channel.experiment_session is None

    channel.new_user_message(base_messages.unsupported_content_type_message())
    assert channel.experiment_session is not None
    assert channel.text_sent == [bot_response]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("voice_behaviour", "user_message", "voice_response_expected"),
    [
        (VoiceResponseBehaviours.ALWAYS, base_messages.text_message(), True),
        (VoiceResponseBehaviours.ALWAYS, base_messages.audio_message(), True),
        (VoiceResponseBehaviours.NEVER, base_messages.text_message(), False),
        (VoiceResponseBehaviours.NEVER, base_messages.audio_message(), False),
        (VoiceResponseBehaviours.RECIPROCAL, base_messages.text_message(), False),
        (VoiceResponseBehaviours.RECIPROCAL, base_messages.audio_message(), True),
    ],
)
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_voice_transcript")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._reply_voice_message")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_bot_response")
def test_voice_response_behaviour(
    get_llm_response,
    _reply_voice_message,
    send_text_to_user,
    get_voice_transcript,
    voice_behaviour,
    user_message,
    voice_response_expected,
    test_channel,
):
    get_voice_transcript.return_value = "Hello bot. Please assist me"
    get_llm_response.return_value = ChatMessage(content="Hello user. No"), None
    experiment = test_channel.experiment
    experiment.voice_response_behaviour = voice_behaviour
    experiment.save()

    test_channel.new_user_message(user_message)

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
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._reply_voice_message")
@patch("apps.chat.bots.EventBot.get_user_message")
def test_failed_transcription_informs_the_user(
    _get_user_message,
    _reply_voice_message,
    send_text_to_user,
    voice_behaviour,
    voice_response_expected,
    test_channel,
):
    """When we fail to transcribe the user's voice message, we should inform them"""

    _get_user_message.return_value = "Sorry, we could not transcribe your message"
    experiment = test_channel.experiment
    experiment.voice_response_behaviour = voice_behaviour
    experiment.save()

    with pytest.raises(Exception, match="Nope"):
        with patch(
            "apps.channels.tests.test_base_channel_behavior.TestChannel._get_voice_transcript",
            side_effect=Exception("Nope"),
        ):
            test_channel.new_user_message(base_messages.audio_message())

    assert _reply_voice_message.called == voice_response_expected
    assert send_text_to_user.called == (not voice_response_expected)


@pytest.mark.django_db()
@patch("apps.chat.bots.EventBot.get_user_message")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_message_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.is_message_type_supported")
def test_any_failure_informs_users(
    is_message_type_supported, send_message_to_user, _get_user_message, test_channel, caplog
):
    """
    Any failure should try and inform the user that something went wrong. The method that does the informing should
    not fail.
    """

    is_message_type_supported.side_effect = Exception("Random error")
    # The generate response should fail, causing the default error message to be sent
    _get_user_message.side_effect = Exception("Generation error")

    with pytest.raises(Exception, match="Random error"):
        test_channel.new_user_message(base_messages.text_message())

    assert send_message_to_user.call_args[0][0] == DEFAULT_ERROR_RESPONSE_TEXT

    assert caplog.records[0].msg == (
        "Something went wrong while trying to generate an appropriate error message for the user"
    )


@pytest.mark.django_db()
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_voice_transcript")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._reply_voice_message")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_bot_response")
def test_reply_with_text_when_synthetic_voice_not_specified(
    get_llm_response,
    _reply_voice_message,
    send_text_to_user,
    get_voice_transcript,
    test_channel,
):
    get_voice_transcript.return_value = "Hello bot. Please assist me"
    get_llm_response.return_value = ChatMessage(content="Hello user. No"), None
    experiment = test_channel.experiment
    experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    # Let's remove the synthetic voice and see what happens
    experiment.synthetic_voice = None
    experiment.save()

    test_channel.new_user_message(base_messages.text_message())

    _reply_voice_message.assert_not_called()
    send_text_to_user.assert_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("message_func", "message_type"),
    [(base_messages.audio_message, "voice"), (base_messages.text_message, "text")],
)
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._add_message_to_history", Mock())
def test_user_query_extracted_for_pre_conversation_flow(message_func, message_type):
    """The user query need to be available during the pre-conversation flow. Simply looking at `message_text` for
    this is erroneous, since it will not be available when the user sends a voice message.

    This test simply makes sure that we are able to get the user query when we need it.
    """
    experiment = ExperimentFactory(conversational_consent_enabled=True, seed_message="Hi human")
    experiment_session = ExperimentSessionFactory(experiment=experiment)

    channel = TestChannel(experiment, ExperimentChannelFactory(experiment=experiment))
    channel.experiment_session = experiment_session
    pre_survey = experiment.pre_survey
    assert pre_survey

    with (
        patch.object(channel, "_get_voice_transcript") as _get_voice_transcript,
        patch.object(channel, "_inform_user_of_error") as _inform_user_of_error,
    ):
        _get_voice_transcript.return_value = "Hi botty"

        message = message_func()
        channel.new_user_message(message)
        if message_type == "voice":
            _get_voice_transcript.assert_called()
        elif message_type == "text":
            _get_voice_transcript.assert_not_called()

        _inform_user_of_error.assert_not_called()


@pytest.mark.django_db()
@override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
@pytest.mark.parametrize("platform", [platform for platform, _ in ChannelPlatform.choices])
def test_all_channels_can_be_instantiated_from_a_session(platform, twilio_provider):
    """This test checks all channel types and makes sure that we can instantiate each one by calling
    `ChannelBase.from_experiment_session`. For the sake of ease, we assume all platforms uses the Twilio
    messenging provider.
    """
    if platform == ChannelPlatform.EVALUATIONS:
        pytest.skip("Evaluations channel can't be instantiated from a session")
    session = ExperimentSessionFactory(experiment_channel__platform=platform)
    ParticipantData.objects.create(
        team=session.team,
        experiment=session.experiment,
        data={},
        participant=session.participant,
        system_metadata={"consent": True},
    )
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
def test_participant_reused_across_experiments():
    """A single participant should be linked to multiple sessions per team"""
    chat_id = "123"

    # User chats to experiment 1
    experiment1 = ExperimentFactory()
    team1 = experiment1.team
    tele_channel1 = TestChannel(experiment1, ExperimentChannelFactory(experiment=experiment1))
    _send_user_message_on_channel(tele_channel1, base_messages.text_message(participant_id=chat_id))

    # User chats to experiment 2 that is in the same teamparticipant_id
    experiment2 = ExperimentFactory(team=team1)
    tele_channel2 = TestChannel(experiment2, ExperimentChannelFactory(experiment=experiment2))
    _send_user_message_on_channel(tele_channel2, base_messages.text_message(participant_id=chat_id))

    # User chats to experiment 3 that is in a different team
    experiment3 = ExperimentFactory()
    team2 = experiment3.team
    tele_channel3 = TestChannel(experiment3, ExperimentChannelFactory(experiment=experiment3))
    _send_user_message_on_channel(tele_channel3, base_messages.text_message(participant_id=chat_id))

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
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_voice_transcript")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_voice_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_bot_response")
def test_voice_response_with_urls(
    get_llm_response,
    send_voice_to_user,
    send_text_to_user,
    get_voice_transcript,
    get_speech_service,
    test_channel,
):
    get_voice_transcript.return_value = "Hello bot. Give me a URL"
    get_llm_response.return_value = [
        ChatMessage.objects.create(
            content=(
                "Here are two urls for you: [this](http://example.co.za?key1=1&key2=2) and [https://some.com](https://some.com)"
            ),
            chat=Chat.objects.create(team=test_channel.experiment.team),
        ),
        None,
    ]
    experiment = test_channel.experiment
    experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    experiment.save()

    test_channel.new_user_message(base_messages.text_message())

    assert send_voice_to_user.called is True

    text_message = send_text_to_user.mock_calls[0].args[0]
    assert "http://example.co.za?key1=1&key2=2" in text_message
    assert "https://some.com" in text_message


@pytest.mark.django_db()
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel._get_voice_transcript")
@patch("apps.service_providers.models.VoiceProvider.get_speech_service")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_voice_to_user")
def test_voice_tag_created_on_message(
    send_voice_to_user, send_text_to_user, get_speech_service, get_voice_transcript, test_channel
):
    get_voice_transcript.return_value = "I'm groot"

    experiment = test_channel.experiment
    experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
    experiment.save()

    session = ExperimentSessionFactory()
    test_channel.experiment_session = session
    test_channel.new_user_message(base_messages.audio_message())
    query_messages = session.chat.messages.all()

    assert query_messages.count() == 2
    bot_message = session.chat.messages.get(message_type=ChatMessageType.AI)
    user_message = session.chat.messages.get(message_type=ChatMessageType.HUMAN)
    assert any([tag for tag in user_message.tags.all() if tag.category == TagCategories.MEDIA_TYPE])
    assert any([tag for tag in bot_message.tags.all() if tag.category == TagCategories.MEDIA_TYPE])


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
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_voice_to_user")
@patch("apps.channels.tests.test_base_channel_behavior.TestChannel.send_text_to_user")
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

    channel = TestChannel(session.experiment, session.experiment_channel, session)

    bot_message = "Hi user"

    channel.send_message_to_user(bot_message)

    if expected_message_type == "text":
        send_text_to_user.assert_called()
        assert send_text_to_user.call_args[0][0] == bot_message
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
def test_participant_authorization(whitelist, is_external_user, identifier, is_allowed, test_channel):
    message = base_messages.text_message(participant_id=identifier)
    experiment = test_channel.experiment
    if not is_external_user:
        MembershipFactory(team=experiment.team, user__email=identifier)

    experiment.participant_allowlist = whitelist
    test_channel.message = message
    assert test_channel._participant_is_allowed() == is_allowed

    if not is_allowed:
        resp = test_channel.new_user_message(message)
        assert resp.content == "Sorry, you are not allowed to chat to this bot"
        assert test_channel.text_sent[0] == "Sorry, you are not allowed to chat to this bot"


@pytest.mark.django_db()
def test_participant_identifier_determination():
    """
    Test participant identifier is fetched from the cached value, otherwise from the session, and lastly from the
    user message
    """
    session = ExperimentSessionFactory(participant__identifier="Alpha")
    exp_channel = ExperimentChannelFactory(experiment=session.experiment)
    channel_base = TestChannel(experiment=session.experiment, experiment_channel=exp_channel)
    channel_base.message = base_messages.text_message(participant_id="Beta")

    assert channel_base.participant_identifier == "Beta"
    assert channel_base._participant_identifier == "Beta"
    # Reset cached value
    channel_base._participant_identifier = None
    # Set the session and check that the identifier is fetched from the session
    channel_base.experiment_session = session
    assert channel_base.participant_identifier == "Alpha"


def test_new_sessions_are_linked_to_the_working_experiment(experiment):
    working_version = experiment
    channel = ExperimentChannelFactory(experiment=working_version)
    new_version = working_version.create_new_version()

    test_channel = TestChannel(experiment=new_version, experiment_channel=channel)
    _send_user_message_on_channel(test_channel, base_messages.text_message())

    # Check that the working experiment is linked to the session
    assert ExperimentSession.objects.filter(experiment=working_version).exists()
    assert not ExperimentSession.objects.filter(experiment=new_version).exists()


def test_can_start_a_session_with_working_experiment(experiment):
    assert experiment.is_a_version is False
    channel = ExperimentChannelFactory(experiment=experiment)
    session = ChannelBase.start_new_session(experiment, channel, participant_identifier="testy-pie")
    assert session.experiment == experiment


def test_cannot_start_a_session_with_an_experiment_version(experiment):
    channel = ExperimentChannelFactory(experiment=experiment)
    new_version = experiment.create_new_version()
    assert new_version.is_a_version is True
    with pytest.raises(VersionedExperimentSessionsNotAllowedException):
        ChannelBase.start_new_session(new_version, channel, participant_identifier="testy-pie")


@pytest.mark.parametrize("new_session", [True, False])
def test_ensure_session_exists_for_participant(new_session, experiment):
    experiment_channel = ExperimentChannelFactory(experiment=experiment)
    if new_session:
        ExperimentSessionFactory(
            experiment=experiment, participant__identifier="testy-pie", experiment_channel=experiment_channel
        )
    channel_base = TestChannel(experiment=experiment, experiment_channel=experiment_channel)
    channel_base.ensure_session_exists_for_participant(identifier="testy-pie", new_session=new_session)

    if new_session:
        assert ExperimentSession.objects.filter(participant__identifier="testy-pie").count() == 2
    else:
        assert ExperimentSession.objects.filter(participant__identifier="testy-pie").count() == 1


@pytest.mark.django_db()
def test_supported_and_unsupported_attachments(experiment):
    """
    Test that the bot's response is sent along with a message for each supported attachment. Unsupported files
    should be appended as links to the bot's response.
    """

    class CustomChannel(TestChannel):
        @property
        def supports_multimedia(self):
            return True

        def _can_send_file(self, file: File):
            return file.name in ["f1", "f2"]

    session = ExperimentSessionFactory(experiment=experiment)
    channel = CustomChannel(experiment, experiment_channel=Mock(), experiment_session=session)
    channel.send_text_to_user = Mock()
    channel.send_file_to_user = Mock()

    file1 = FileFactory(name="f1", content_type="image/jpeg")
    file2 = FileFactory(name="f2", content_type="image/jpeg")
    # This file is too large to be sent as a message and should be sent as a link
    file3 = Mock(
        spec=File, name="f3", content_type="image/jpeg", download_link=lambda *args, **kwargs: "https://example.com"
    )

    channel.send_message_to_user("Hi there", files=[file1, file2, file3])

    assert channel.send_text_to_user.call_args[0][0] == f"Hi there\n\n{file3.name}\nhttps://example.com\n"
    assert channel.send_file_to_user.mock_calls[0].args[0] == file1
    assert channel.send_file_to_user.mock_calls[1].args[0] == file2


@pytest.mark.django_db()
def test_chat_message_returned_for_cancelled_generate():
    session = ExperimentSessionFactory()
    channel = TestChannel(session.experiment, None, session)
    channel._add_message = Mock()
    channel._new_user_message = Mock()
    channel._new_user_message.side_effect = GenerationCancelled(output="Cancelled")
    channel.message = base_messages.text_message("123", "hi")
    response = channel.new_user_message(channel.message)

    assert type(response) is ChatMessage
