from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.channels.stages.terminal import ResponseSendingStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.whatsapp_channel import WhatsappCallbacks, WhatsappChannel, WhatsappSender
from apps.chat.exceptions import ServiceWindowExpiredException

BSUID = "US.13491208655302741918"
PHONE = "+27456897512"


def _bound_context(phone_number):
    participant = SimpleNamespace(remote_id=phone_number or "")
    return make_context(participant_identifier=BSUID, participant=participant)


class TestWhatsappSenderRecipientResolution:
    """WhatsApp sends go to the phone number stored on the participant's remote_id, since the
    participant identifier is a (non-sendable) BSUID. When no phone is stored, fall back to the
    identifier."""

    def test_send_text_uses_stored_phone_number(self):
        service = MagicMock()
        sender = WhatsappSender(service=service, from_number="phone123")
        sender.bind(_bound_context(PHONE))

        sender.send_text("hi", recipient=BSUID)

        assert service.send_text_message.call_args.kwargs["to"] == PHONE

    def test_send_text_falls_back_to_identifier_when_no_phone(self):
        service = MagicMock()
        sender = WhatsappSender(service=service, from_number="phone123")
        sender.bind(_bound_context(None))

        sender.send_text("hi", recipient=BSUID)

        assert service.send_text_message.call_args.kwargs["to"] == BSUID

    def test_send_voice_uses_stored_phone_number(self):
        service = MagicMock()
        sender = WhatsappSender(service=service, from_number="phone123")
        sender.bind(_bound_context(PHONE))

        sender.send_voice(MagicMock(), recipient=BSUID)

        assert service.send_voice_message.call_args.kwargs["to"] == PHONE

    def test_send_file_uses_stored_phone_number(self):
        service = MagicMock()
        sender = WhatsappSender(service=service, from_number="phone123")
        sender.bind(_bound_context(PHONE))

        sender.send_file(MagicMock(), recipient=BSUID, session_id=1)

        assert service.send_file_to_user.call_args.kwargs["to"] == PHONE


class TestWhatsappCallbacksRecipientResolution:
    def test_echo_transcript_uses_stored_phone_number(self):
        service = MagicMock()
        callbacks = WhatsappCallbacks(service=service, from_number="phone123")
        callbacks.bind(_bound_context(PHONE))

        callbacks.echo_transcript(recipient=BSUID, transcript="hi")

        assert service.send_text_message.call_args.kwargs["to"] == PHONE


class TestShouldVoiceFallbackToText:
    """Voice can never be delivered via template after the 24h window closes, but text can,
    so WhatsApp approves the text fallback for exactly that exception."""

    def _make_channel(self):
        return WhatsappChannel(experiment=MagicMock(), experiment_channel=MagicMock())

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            pytest.param(ServiceWindowExpiredException("window expired"), True, id="service-window-expired"),
            pytest.param(RuntimeError("network error"), False, id="other-exception"),
        ],
    )
    def test_should_voice_fallback_to_text(self, exc, expected):
        channel = self._make_channel()
        assert channel._should_voice_fallback_to_text(exc) is expected

    def test_predicate_is_wired_into_response_sending_stage(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()
        stage = next(s for s in pipeline.terminal_stages if isinstance(s, ResponseSendingStage))
        assert stage._should_voice_fallback_to_text == channel._should_voice_fallback_to_text
