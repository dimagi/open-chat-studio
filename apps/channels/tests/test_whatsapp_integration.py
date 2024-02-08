import json

import pytest
from mock import patch

from apps.channels.datamodels import TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.fixture
def turn_io_provider():
    return MessagingProviderFactory(name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"})


@pytest.fixture
def turnio_whatsapp_channel(turn_io_provider):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=turn_io_provider,
        experiment__team=turn_io_provider.team,
        extra_data={"number": "+14155238886"},
    )


@pytest.fixture
def twilio_provider():
    return MessagingProviderFactory(
        name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
    )


@pytest.fixture
def twilio_whatsapp_channel(twilio_provider):
    ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=twilio_provider,
        experiment__team=twilio_provider.team,
        extra_data={"number": "+14155238886"},
    )


class TwilioMessages:
    @staticmethod
    def text_message():
        return json.dumps(
            {
                "SmsMessageSid": "DDDDDDDDDDDDDdd",
                "NumMedia": "0",
                "ProfileName": "Chris Smit",
                "SmsSid": "CCCCCCCCCCCCCCCCCCCCCCCCCC",
                "WaId": "27456897512",
                "SmsStatus": "received",
                "Body": "Dobroye utro",
                "To": "whatsapp:+14155238886",
                "NumSegments": "1",
                "ReferralNumMedia": "0",
                "MessageSid": "BBBBBBBBBB",
                "AccountSid": "AAAAAAAAAAAAA",
                "From": "whatsapp:+27456897512",
                "ApiVersion": "2010-04-01",
            }
        )

    @staticmethod
    def audio_message():
        message = TwilioMessages.text_message()
        message_dict = json.loads(message)
        message_dict["MediaContentType0"] = "audio/ogg"
        message_dict["MediaUrl0"] = "http://example.com/media"
        return json.dumps(message_dict)


class TurnIOMessages:
    @staticmethod
    def text_message():
        return {
            "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
            "messages": [
                {
                    "_vnd": {
                        "v1": {
                            "author": {"id": "27456897512", "name": "User", "type": "OWNER"},
                            "card_uuid": "None",
                            "chat": {
                                "assigned_to": "None",
                                "contact_uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                                "inserted_at": "2024-01-25T09:02:46.684610Z",
                                "owner": "+27456897512",
                                "permalink": "https://whatsapp.turn.io/app/c/08a64841-32123-4111-b91f-4ff36d676c1c",
                                "state": "OPEN",
                                "state_reason": "Re-opened by inbound message.",
                                "unread_count": 1,
                                "updated_at": "2024-01-31T14:01:56.486313Z",
                                "uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                            },
                            "direction": "inbound",
                            "faq_uuid": "None",
                            "in_reply_to": "None",
                            "inserted_at": "2024-01-31T14:01:56.467839Z",
                            "labels": [],
                            "last_status": "None",
                            "last_status_timestamp": "None",
                            "on_fallback_channel": False,
                            "rendered_content": "None",
                            "uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                        }
                    },
                    "from": "27456897512",
                    "id": "ABCDEFGHIJKL_Ags-sF0gx5ts0DDMxw",
                    "text": {"body": "Hi there!"},
                    "timestamp": "1706709716",
                    "type": "text",
                }
            ],
        }

    @staticmethod
    def audio_message():
        return {
            "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
            "messages": [
                {
                    "_vnd": {
                        "v1": {
                            "author": {"id": "27456897512", "name": "Chris Smit", "type": "OWNER"},
                            "card_uuid": "None",
                            "chat": {
                                "assigned_to": "None",
                                "contact_uuid": "eeb51508-4ff0-4ca4-9bf8-69e548b1ceb3",
                                "inserted_at": "2024-01-25T09:02:46.684610Z",
                                "owner": "+27456897512",
                                "permalink": "https://whatsapp.turn.io/app/c/08a64841-10df-4c11-b81f-4ec36d616c1c",
                                "state": "OPEN",
                                "state_reason": "Re-opened by inbound message.",
                                "unread_count": 31,
                                "updated_at": "2024-02-08T12:07:46.091528Z",
                                "uuid": "08a64841-10df-4c11-b81f-4ec36d616c1c",
                            },
                            "direction": "inbound",
                            "faq_uuid": "None",
                            "in_reply_to": "None",
                            "inserted_at": "2024-02-08T12:07:45.891699Z",
                            "labels": [],
                            "last_status": "None",
                            "last_status_timestamp": "None",
                            "on_fallback_channel": False,
                            "rendered_content": "None",
                            "uuid": "bd788d51-f3e1-11ff-e31d-0fc372a61d66",
                        }
                    },
                    "from": "27456897512",
                    "id": "ABGKLKLKLZd_Ags-DSDSdsWQUpsLqg",
                    "timestamp": "1707394065",
                    "type": "voice",
                    "voice": {
                        "id": "180e1c3f-ae50-481b-a9f0-7c698233965f",
                        "mime_type": "audio/ogg; codecs=opus",
                        "sha256": "407d8ac9d98ddddddd78c7bae4179ea131b55740214ccc42373c85d63aeb55b7",
                        "status": "downloaded",
                    },
                }
            ],
        }


class TestTwilio:
    @pytest.mark.parametrize(
        "message, message_type", [(TwilioMessages.text_message(), "text"), (TwilioMessages.audio_message(), "voice")]
    )
    def test_parse_messages(self, message, message_type):
        whatsapp_message = TwilioMessage.model_validate(json.loads(message))
        assert whatsapp_message.chat_id == whatsapp_message.from_number
        if message_type == "text":
            assert whatsapp_message.content_type == MESSAGE_TYPES.TEXT
            assert whatsapp_message.media_url == None
        else:
            assert whatsapp_message.content_type == MESSAGE_TYPES.VOICE
            assert whatsapp_message.media_url == "http://example.com/media"

    @pytest.mark.parametrize("incoming_message", [TwilioMessages.text_message(), TwilioMessages.audio_message()])
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TwilioService.send_whatsapp_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_twilio_uses_whatsapp_channel_implementation(
        self,
        get_llm_response_mock,
        send_whatsapp_text_message,
        get_voice_transcript_mock,
        db,
        incoming_message,
        twilio_whatsapp_channel,
    ):
        """Test that the twilio integration can use the WhatsappChannel implementation"""
        get_llm_response_mock.return_value = "Hi"
        get_voice_transcript_mock.return_value = "Hi"
        handle_twilio_message(message_data=incoming_message)
        send_whatsapp_text_message.assert_called()


class TestTurnio:
    @pytest.mark.parametrize(
        "message, message_type", [(TurnIOMessages.text_message(), "text"), (TurnIOMessages.audio_message(), "voice")]
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

    @pytest.mark.parametrize("incoming_message", [TurnIOMessages.text_message(), TurnIOMessages.audio_message()])
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_whatsapp_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_turnio_whatsapp_channel_implementation(
        self,
        _get_llm_response,
        send_whatsapp_text_message,
        get_voice_transcript_mock,
        db,
        turnio_whatsapp_channel,
        incoming_message,
    ):
        """Test that the turnio integration can use the WhatsappChannel implementation"""
        _get_llm_response.return_value = "Hi"
        get_voice_transcript_mock.return_value = "Hi"
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        send_whatsapp_text_message.assert_called()

    @patch("apps.chat.channels.WhatsappChannel.new_user_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_unsupported_message_type_does_nothing(
        self, get_llm_response_mock, new_user_message_mock, db, turnio_whatsapp_channel
    ):
        """Test that nothing happens for unsupported message types"""
        get_llm_response_mock.return_value = "Hi"
        incoming_message = TurnIOMessages.text_message()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        new_user_message_mock.assert_not_called()
