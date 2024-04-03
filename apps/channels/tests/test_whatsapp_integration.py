import json
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse

from apps.channels.datamodels import TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.service_providers.models import MessagingProviderType
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

from .message_examples import turnio_messages, twilio_messages


@pytest.fixture()
def turn_io_provider():
    return MessagingProviderFactory(name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"})


@pytest.fixture()
def turnio_whatsapp_channel(turn_io_provider):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=turn_io_provider,
        experiment__team=turn_io_provider.team,
        extra_data={"number": "+14155238886"},
    )


@pytest.fixture()
def twilio_provider(db):
    return MessagingProviderFactory(
        name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
    )


@pytest.fixture()
def _twilio_whatsapp_channel(twilio_provider):
    ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=twilio_provider,
        experiment__team=twilio_provider.team,
        extra_data={"number": "+14155238886"},
    )


class TestTwilio:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [(twilio_messages.Whatsapp.text_message(), "text"), (twilio_messages.Whatsapp.audio_message(), "voice")],
    )
    def test_parse_messages(self, message, message_type):
        whatsapp_message = TwilioMessage.parse(json.loads(message))
        assert whatsapp_message.platform == ChannelPlatform.WHATSAPP
        assert whatsapp_message.chat_id == whatsapp_message.from_
        if message_type == "text":
            assert whatsapp_message.content_type == MESSAGE_TYPES.TEXT
            assert whatsapp_message.media_url is None
        else:
            assert whatsapp_message.content_type == MESSAGE_TYPES.VOICE
            assert whatsapp_message.media_url == "http://example.com/media"

    @pytest.mark.usefixtures("_twilio_whatsapp_channel")
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [(twilio_messages.Whatsapp.text_message(), "text"), (twilio_messages.Whatsapp.audio_message(), "audio")],
    )
    @patch("apps.service_providers.messaging_service.settings.AWS_ACCESS_KEY_ID", "123")
    @patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TwilioService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_twilio_uses_whatsapp_channel_implementation(
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
        with patch("apps.service_providers.messaging_service.TwilioService.s3_client"), patch(
            "apps.service_providers.messaging_service.TwilioService.client"
        ):
            get_llm_response_mock.return_value = "Hi"
            get_voice_transcript_mock.return_value = "Hi"

            handle_twilio_message(message_data=incoming_message)

            if message_type == "text":
                send_text_message.assert_called()
            elif message_type == "audio":
                send_voice_message.assert_called()


class TestTurnio:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [(turnio_messages.text_message(), "text"), (turnio_messages.voice_message(), "voice")],
    )
    def test_parse_text_message(self, message, message_type):
        message = TurnWhatsappMessage.parse(message)
        assert message.chat_id == "27456897512"
        if message_type == "text":
            assert message.body == "Hi there!"
            assert message.content_type == MESSAGE_TYPES.TEXT
        else:
            assert message.media_id == "180e1c3f-ae50-481b-a9f0-7c698233965f"
            assert message.content_type == MESSAGE_TYPES.VOICE

    @pytest.mark.parametrize("incoming_message", [turnio_messages.text_message(), turnio_messages.voice_message()])
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_turnio_whatsapp_channel_implementation(
        self,
        _get_llm_response,
        send_text_message,
        get_voice_transcript_mock,
        db,
        turnio_whatsapp_channel,
        incoming_message,
    ):
        """Test that the turnio integration can use the WhatsappChannel implementation"""
        _get_llm_response.return_value = "Hi"
        get_voice_transcript_mock.return_value = "Hi"
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        send_text_message.assert_called()

    @patch("apps.chat.channels.ChannelBase._handle_supported_message")
    @patch("apps.chat.channels.ChannelBase._handle_unsupported_message")
    def test_unsupported_message_type_does_nothing(
        self, _handle_unsupported_message, _handle_supported_message, db, turnio_whatsapp_channel
    ):
        """Test that unsupported messages are not"""
        incoming_message = turnio_messages.text_message()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        _handle_unsupported_message.assert_called()
        _handle_supported_message.assert_not_called()

    @pytest.mark.django_db()
    @pytest.mark.parametrize("message", [turnio_messages.outbound_message(), turnio_messages.status_message()])
    @patch("apps.channels.tasks.handle_turn_message")
    def test_outbound_and_status_messages_ignored(self, handle_turn_message_task, message, client):
        url = reverse("channels:new_turn_message", kwargs={"experiment_id": str(uuid4())})
        response = client.post(url, data=message, content_type="application/json")
        response.status_code == 200
        handle_turn_message_task.assert_not_called()
