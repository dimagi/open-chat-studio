from io import BytesIO
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.tests.message_examples.base_messages import text_message
from apps.chat.channels import MESSAGE_TYPES


class StubSender(ChannelSender):
    """Captures outbound messages for assertions."""

    def __init__(self):
        self.text_messages = []
        self.voice_messages = []
        self.files_sent = []

    def send_text(self, text, recipient):
        self.text_messages.append((text, recipient))

    def send_voice(self, audio, recipient):
        self.voice_messages.append((audio, recipient))

    def send_file(self, file, recipient, session_id):
        self.files_sent.append((file, recipient, session_id))


class StubCallbacks(ChannelCallbacks):
    """Records callback invocations."""

    def __init__(self):
        self.transcription_started_calls = []
        self.echo_transcript_calls = []
        self.submit_input_calls = []
        self.transcription_finished_calls = []

    def transcription_started(self, recipient):
        self.transcription_started_calls.append(recipient)

    def transcription_finished(self, recipient, transcript):
        self.transcription_finished_calls.append((recipient, transcript))

    def echo_transcript(self, recipient, transcript):
        self.echo_transcript_calls.append((recipient, transcript))

    def submit_input_to_llm(self, recipient):
        self.submit_input_calls.append(recipient)

    def get_message_audio(self, message):
        return BytesIO(b"fake_audio")


class StubChannel(ChannelBase):
    """Minimal concrete channel for integration tests."""

    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    def __init__(self, *args, capabilities=None, **kwargs):
        self._override_capabilities = capabilities
        self._sender = StubSender()
        self._callbacks = StubCallbacks()
        super().__init__(*args, **kwargs)

    def _get_sender(self):
        return self._sender

    def _get_callbacks(self):
        return self._callbacks

    def _get_capabilities(self):
        return self._override_capabilities or ChannelCapabilities(
            supports_voice=True,
            supports_files=False,
            supports_conversational_consent=True,
            supports_static_triggers=True,
            supported_message_types=[MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
        )

    @property
    def text_sent(self):
        return [t for t, _ in self._sender.text_messages]

    @property
    def voice_sent(self):
        return [a for a, _ in self._sender.voice_messages]


def make_trace_service():
    """Create a MagicMock trace service with context manager support."""
    trace_service = MagicMock()
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    trace_service.span.return_value = span
    trace = MagicMock()
    trace.__enter__ = MagicMock(return_value=trace)
    trace.__exit__ = MagicMock(return_value=False)
    trace_service.trace.return_value = trace
    trace_service.get_trace_metadata.return_value = {}
    return trace_service


def make_capabilities(**overrides):
    """Create a default ChannelCapabilities, overridable with kwargs."""
    defaults = {
        "supports_voice": True,
        "supports_files": False,
        "supports_conversational_consent": True,
        "supports_static_triggers": True,
        "supported_message_types": [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
    }
    defaults.update(overrides)
    return ChannelCapabilities(**defaults)


def make_context(
    *,
    message=None,
    experiment=None,
    experiment_channel=None,
    experiment_session=None,
    sender=None,
    callbacks=None,
    capabilities=None,
    participant_identifier="test_user_123",
    participant_allowed=True,
    user_query=None,
    bot_response=None,
    early_exit_response=None,
    sending_exception=None,
    channel_context=None,
    human_message=None,
    bot=None,
    formatted_message=None,
    voice_audio=None,
    additional_text_message=None,
    files_to_send=None,
    unsupported_files=None,
    human_message_tags=None,
    processing_errors=None,
    trace_service=None,
    **extra,
):
    """Build a minimal MessageProcessingContext for stage unit tests.
    Uses MagicMock for experiment/channel by default (no DB)."""
    return MessageProcessingContext(
        message=message or text_message(),
        experiment=experiment or MagicMock(),
        experiment_channel=experiment_channel or MagicMock(),
        callbacks=callbacks or StubCallbacks(),
        sender=sender or StubSender(),
        capabilities=capabilities or make_capabilities(),
        trace_service=trace_service or make_trace_service(),
        experiment_session=experiment_session,
        participant_identifier=participant_identifier,
        participant_allowed=participant_allowed,
        user_query=user_query,
        bot_response=bot_response,
        early_exit_response=early_exit_response,
        sending_exception=sending_exception,
        channel_context=channel_context or {},
        human_message=human_message,
        bot=bot,
        formatted_message=formatted_message,
        voice_audio=voice_audio,
        additional_text_message=additional_text_message,
        files_to_send=files_to_send or [],
        unsupported_files=unsupported_files or [],
        human_message_tags=human_message_tags or [],
        processing_errors=processing_errors or [],
        **extra,
    )


@pytest.fixture()
def test_sender():
    return StubSender()


@pytest.fixture()
def test_callbacks():
    return StubCallbacks()


@pytest.fixture()
def trace_service():
    return make_trace_service()
