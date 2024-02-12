import json
from unittest.mock import patch

from apps.channels.datamodels import TurnWhatsappMessage, WhatsappMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_turn_message
from apps.chat.channels import MESSAGE_TYPES
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


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


class TestTwilio:
    def test_parse_text_message(self):
        incoming_message = TwilioMessages.text_message()
        whatsapp_message = WhatsappMessage.model_validate(json.loads(incoming_message))
        assert whatsapp_message.chat_id == whatsapp_message.from_number
        assert whatsapp_message.content_type == MESSAGE_TYPES.TEXT
        assert whatsapp_message.media_url is None

    def test_parse_media_message(self):
        incoming_message = TwilioMessages.audio_message()
        whatsapp_message = WhatsappMessage.model_validate(json.loads(incoming_message))
        assert whatsapp_message.chat_id == whatsapp_message.from_number
        assert whatsapp_message.content_type == MESSAGE_TYPES.VOICE
        assert whatsapp_message.media_url == "http://example.com/media"

    @patch("apps.service_providers.messaging_service.TwilioService.send_whatsapp_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_twilio_uses_whatsapp_channel_implementation(self, _get_llm_response, send_whatsapp_text_message, db):
        """Test that the twilio integration can use the WhatsappChannel implementation"""
        _get_llm_response.return_value = "Hi"
        provider = MessagingProviderFactory(
            name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
        )
        channel = ExperimentChannelFactory(
            platform=ChannelPlatform.WHATSAPP, messaging_provider=provider, experiment__team=provider.team
        )
        incoming_message = TurnIOMessages.text_message()
        handle_turn_message(experiment_id=channel.experiment.public_id, message_data=incoming_message)
        send_whatsapp_text_message.assert_called()


class TestTurnio:
    def test_parse_text_message(self):
        message = TurnWhatsappMessage.parse(TurnIOMessages.text_message())
        assert message.chat_id == "27456897512"
        assert message.body == "Hi there!"
        assert message.content_type == MESSAGE_TYPES.TEXT

    @patch("apps.service_providers.messaging_service.TurnIOService.send_whatsapp_text_message")
    @patch("apps.chat.channels.WhatsappChannel._get_llm_response")
    def test_turnio_whatsapp_channel_implementation(self, _get_llm_response, send_whatsapp_text_message, db):
        """Test that the turnio integration can use the WhatsappChannel implementation"""
        _get_llm_response.return_value = "Hi"
        provider = MessagingProviderFactory(
            name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"}
        )
        channel = ExperimentChannelFactory(
            platform=ChannelPlatform.WHATSAPP, messaging_provider=provider, experiment__team=provider.team
        )
        incoming_message = TurnIOMessages.text_message()
        handle_turn_message(experiment_id=channel.experiment.public_id, message_data=incoming_message)
        send_whatsapp_text_message.assert_called()
