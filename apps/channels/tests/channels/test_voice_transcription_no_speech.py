"""End-to-end behaviour for a voice note the transcriber found no speech in.

Runs a real message through ``ChannelBase.new_user_message`` against the DB, which is
what checks that the provider, the stage and the pipeline connect. The tests either
side of this one each mock one of those boundaries.
"""

from contextlib import contextmanager
from unittest.mock import patch

import azure.cognitiveservices.speech as speechsdk
import pytest

from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.tests.message_examples import base_messages
from apps.chat.exceptions import NoSpeechReason, UserReportableError
from apps.ocs_notifications.models import NotificationEvent
from apps.service_providers.models import VoiceProviderType
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory

from .conftest import StubChannel, make_trace_service


@pytest.fixture()
def azure_voice_session(db):
    session = ExperimentSessionFactory.create()
    session.experiment.voice_provider = VoiceProviderFactory.create(
        team=session.team,
        type=VoiceProviderType.azure,
        config={"azure_subscription_key": "key", "azure_region": "eastus"},
    )
    session.experiment.save()
    return session


def _channel(session):
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    channel.trace_service = make_trace_service()
    return channel


@contextmanager
def _azure_no_match(reason):
    """Patch the Azure SDK so recognition returns NoMatch with the given reason."""
    with (
        patch.object(speechsdk, "SpeechConfig"),
        patch.object(speechsdk.audio, "AudioConfig"),
        patch.object(speechsdk, "SpeechRecognizer") as recognizer_cls,
    ):
        recognition = recognizer_cls.return_value.recognize_once_async.return_value.get.return_value
        recognition.reason = speechsdk.ResultReason.NoMatch
        recognition.no_match_details.reason = reason
        yield


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("no_match_reason", "expected_reason"),
    [
        pytest.param(speechsdk.NoMatchReason.InitialSilenceTimeout, NoSpeechReason.SILENCE, id="silence"),
        pytest.param(speechsdk.NoMatchReason.NotRecognized, NoSpeechReason.NOT_UNDERSTOOD, id="not-understood"),
    ],
)
@patch("apps.channels.pipeline.EventBot")
def test_no_speech_replies_without_erroring_or_notifying(
    mock_event_bot_cls, azure_voice_session, no_match_reason, expected_reason
):
    mock_event_bot_cls.return_value.get_user_message.return_value = "I could not hear anything"
    channel = _channel(azure_voice_session)

    with _azure_no_match(no_match_reason):
        channel.new_user_message(base_messages.audio_message())

    assert channel.text_sent == ["I could not hear anything"]

    prompt = mock_event_bot_cls.return_value.get_user_message.call_args.args[0]
    assert prompt == MessageProcessingPipeline.NO_SPEECH_PROMPTS[expected_reason]

    assert not NotificationEvent.objects.filter(title="Audio Transcription Failed").exists()


@pytest.mark.django_db()
@patch("apps.channels.pipeline.EventBot")
def test_real_transcription_failure_still_raises_and_notifies(mock_event_bot_cls, azure_voice_session):
    """The other half of the contract: a genuine Azure fault keeps reaching Sentry and the team."""
    mock_event_bot_cls.return_value.get_user_message.return_value = "something went wrong"
    channel = _channel(azure_voice_session)

    with (
        patch.object(speechsdk, "SpeechConfig", side_effect=RuntimeError("invalid subscription key")),
        pytest.raises(UserReportableError, match="Unable to transcribe audio"),
    ):
        channel.new_user_message(base_messages.audio_message())

    assert NotificationEvent.objects.filter(title="Audio Transcription Failed").exists()
