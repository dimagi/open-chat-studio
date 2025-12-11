import json
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from apps.channels.datamodels import TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat, ChatMessage
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.channels import ExperimentChannelFactory

from ...utils.factories.experiment import ExperimentFactory
from .message_examples import twilio_messages


@pytest.fixture()
def _twilio_whatsapp_channel(twilio_provider):
    ExperimentChannelFactory(
        platform=ChannelPlatform.FACEBOOK,
        messaging_provider=twilio_provider,
        experiment__team=twilio_provider.team,
        extra_data={"page_id": "14155238886"},
    )


class TestTwilio:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [
            (twilio_messages.Messenger.text_message(), "text"),
            (twilio_messages.Messenger.audio_message(), "voice"),
        ],
    )
    def test_parse_messages(self, message, message_type):
        whatsapp_message = TwilioMessage.parse(json.loads(message))
        assert whatsapp_message.platform == ChannelPlatform.FACEBOOK
        assert whatsapp_message.participant_id == "27456897512"
        if message_type == "text":
            assert whatsapp_message.content_type == MESSAGE_TYPES.TEXT
            assert whatsapp_message.media_url is None
        else:
            assert whatsapp_message.content_type == MESSAGE_TYPES.VOICE
            assert whatsapp_message.media_url == "http://example.com/media"

    @pytest.mark.usefixtures("_twilio_whatsapp_channel")
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [
            (twilio_messages.Messenger.text_message(), "text"),
            (twilio_messages.Messenger.audio_message(), "audio"),
        ],
    )
    @override_settings(WHATSAPP_S3_AUDIO_BUCKET="123")
    @patch("apps.channels.tasks.validate_twillio_request", Mock())
    @patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TwilioService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.channels.FacebookMessengerChannel._get_bot_response")
    def test_twilio_uses_facebook_channel_implementation(
        self,
        get_llm_response_mock,
        send_text_message,
        send_voice_message,
        get_voice_transcript_mock,
        synthesize_voice_mock,
        incoming_message,
        message_type,
    ):
        """Test that the twilio integration can use the WhatsappChannel implementation"""
        synthesize_voice_mock.return_value = SynthesizedAudio(audio=BytesIO(b"123"), duration=10, format="mp3")
        with (
            patch("apps.service_providers.messaging_service.TwilioService.s3_client"),
            patch("apps.service_providers.messaging_service.TwilioService.client"),
        ):
            experiment = ExperimentFactory(conversational_consent_enabled=True)
            chat = Chat.objects.create(team=experiment.team)
            get_llm_response_mock.return_value = ChatMessage.objects.create(content="Hi", chat=chat)
            get_voice_transcript_mock.return_value = "Hi"

            handle_twilio_message(message_data=incoming_message, request_uri="", signature="")

            if message_type == "text":
                send_text_message.assert_called()
            elif message_type == "audio":
                send_voice_message.assert_called()
