from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

import httpx
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.speech_service import SynthesizedAudio


class TestMetaCloudAPIServiceBSUIDRecipient:
    """Outbound send paths must use Meta's `recipient` field when targeting a BSUID,
    and the `to` field when targeting a phone number.

    See https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids
    """

    BSUID = "US.13491208655302741918"

    def _make_service(self):
        return MetaCloudAPIService(access_token="test_token", business_id="123456")

    def _mock_send_response(self):
        return httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )

    def _mock_upload_response(self):
        return httpx.Response(
            200,
            json={"id": "media_id_abc"},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/media"),
        )

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_message_to_bsuid_uses_recipient_field(self, mock_post):
        mock_post.return_value = self._mock_send_response()
        self._make_service().send_text_message(
            message="Hello",
            from_="phone123",
            to=self.BSUID,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        sent = mock_post.call_args.kwargs["json"]
        assert sent == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "recipient": self.BSUID,
            "type": "text",
            "text": {"body": "Hello"},
        }
        assert "to" not in sent

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_message_to_phone_keeps_to_field(self, mock_post):
        mock_post.return_value = self._mock_send_response()
        self._make_service().send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        sent = mock_post.call_args.kwargs["json"]
        assert sent["to"] == "+27826419977"
        assert "recipient" not in sent

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_to_bsuid_uses_recipient_field(self, mock_post):
        mock_post.return_value = self._mock_send_response()
        self._make_service().send_template_message(
            message="Hi",
            from_="phone123",
            to=self.BSUID,
            platform=ChannelPlatform.WHATSAPP,
        )
        sent = mock_post.call_args.kwargs["json"]
        assert sent["recipient"] == self.BSUID
        assert "to" not in sent

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_voice_message_to_bsuid_uses_recipient_field(self, mock_post):
        mock_post.side_effect = [self._mock_upload_response(), self._mock_send_response()]
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        synthetic_voice.get_audio_bytes.return_value = b"fake-ogg"
        self._make_service().send_voice_message(
            synthetic_voice=synthetic_voice,
            from_="phone123",
            to=self.BSUID,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        sent = mock_post.call_args_list[1].kwargs["json"]
        assert sent["recipient"] == self.BSUID
        assert "to" not in sent

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_file_to_user_bsuid_uses_recipient_field(self, mock_post):
        mock_post.side_effect = [self._mock_upload_response(), self._mock_send_response()]
        file = MagicMock()
        file.content_type = "image/jpeg"
        file.name = "x.jpg"
        file.file.open.return_value.__enter__.return_value = BytesIO(b"data")
        self._make_service().send_file_to_user(
            from_="phone123",
            to=self.BSUID,
            platform=ChannelPlatform.WHATSAPP,
            file=file,
            download_link="https://example.com/x.jpg",
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        sent = mock_post.call_args_list[1].kwargs["json"]
        assert sent["recipient"] == self.BSUID
        assert "to" not in sent
