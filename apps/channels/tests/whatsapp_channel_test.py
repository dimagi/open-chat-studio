import json

from django.test import TestCase

from apps.channels.datamodels import WhatsappMessage
from apps.chat.channels import MESSAGE_TYPES


class TestWhatsappMessage(TestCase):
    def test_parse_text_message(self):
        incoming_message = _whatsapp_text_message()
        whatsapp_message = WhatsappMessage.model_validate(json.loads(incoming_message))
        self.assertEqual(whatsapp_message.chat_id, whatsapp_message.from_number)
        self.assertEqual(whatsapp_message.content_type, MESSAGE_TYPES.TEXT)
        self.assertEqual(whatsapp_message.media_url, None)

    def test_parse_media_message(self):
        incoming_message = _whatsapp_audio_message()
        whatsapp_message = WhatsappMessage.model_validate(json.loads(incoming_message))
        self.assertEqual(whatsapp_message.chat_id, whatsapp_message.from_number)
        self.assertEqual(whatsapp_message.content_type, MESSAGE_TYPES.VOICE)
        self.assertEqual(whatsapp_message.media_url, "http://example.com/media")


def _whatsapp_audio_message():
    message = _whatsapp_text_message()
    message_dict = json.loads(message)
    message_dict["MediaContentType0"] = "audio/ogg"
    message_dict["MediaUrl0"] = "http://example.com/media"
    return json.dumps(message_dict)


def _whatsapp_text_message():
    return json.dumps(
        {
            "SmsMessageSid": "DDDDDDDDDDDDDdd",
            "NumMedia": "0",
            "ProfileName": "Chris Smit",
            "SmsSid": "CCCCCCCCCCCCCCCCCCCCCCCCCC",
            "WaId": "27826419977",
            "SmsStatus": "received",
            "Body": "Dobroye utro",
            "To": "whatsapp:+14155238886",
            "NumSegments": "1",
            "ReferralNumMedia": "0",
            "MessageSid": "BBBBBBBBBB",
            "AccountSid": "AAAAAAAAAAAAA",
            "From": "whatsapp:+27826419977",
            "ApiVersion": "2010-04-01",
        }
    )
