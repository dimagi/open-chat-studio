from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import httpx
import pytest
from django.utils import timezone
from pydantic import ValidationError

from apps.channels.datamodels import MetaCloudAPIMessage, TurnWhatsappMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tests.message_examples import turnio_messages
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.service_providers.exceptions import AudioConversionError
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.service_providers.speech_service import SynthesizedAudio


def test_twilio_messaging_provider(team_with_users):
    _test_messaging_provider(
        team_with_users,
        MessagingProviderType.twilio,
        data={
            "auth_token": "test_token",
            "account_sid": "account_sid",
        },
    )


@pytest.mark.parametrize(
    "config_key",
    [
        "auth_token",
        "account_sid",
    ],
)
def test_twilio_messaging_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = MessagingProviderType.twilio.form_cls(
        team=None,
        data={
            "auth_token": "test_key",
            "account_sid": "test_secret",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_messaging_provider_error(MessagingProviderType.twilio, data=form.cleaned_data)


@pytest.mark.parametrize(
    ("platform", "expected_provider_types"),
    [
        ("whatsapp", ["twilio", "turnio", "meta_cloud_api"]),
        ("telegram", []),
    ],
)
def test_platform_supported_platforms(platform: str, expected_provider_types: list):
    """Test that the correct services are being returned that supports a platform"""
    provider_types = MessagingProviderType.platform_supported_provider_types(platform=ChannelPlatform(platform))
    expected_provider_types = [MessagingProviderType(p_type) for p_type in expected_provider_types]
    assert provider_types == expected_provider_types


def _test_messaging_provider_error(provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(None, data=data)
    assert not form.is_valid()

    with pytest.raises(ValidationError):
        provider_type.get_messaging_service(data)


@pytest.fixture()
def meta_cloud_api_service():
    return MetaCloudAPIService(access_token="test_token", business_id="123456")


def _mock_phone_numbers_response(data):
    return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", "https://test"))


@pytest.mark.parametrize(
    ("api_data", "lookup_number", "expected_id"),
    [
        pytest.param(
            [
                {"id": "111", "display_phone_number": "+1 (212) 555-2368"},
                {"id": "222", "display_phone_number": "+27 81 234 5678"},
            ],
            "+12125552368",
            "111",
            id="formatted_number",
        ),
        pytest.param(
            [{"id": "333", "display_phone_number": "+27812345678"}],
            "+27812345678",
            "333",
            id="e164_number",
        ),
        pytest.param(
            [{"id": "111", "display_phone_number": "+1 212 555 2368"}],
            "+27812345678",
            None,
            id="no_match",
        ),
        pytest.param(
            [],
            "+12125552368",
            None,
            id="empty_response",
        ),
        pytest.param(
            [
                {"id": "111", "display_phone_number": "not-a-number"},
                {"id": "222", "display_phone_number": "+27 81 234 5678"},
            ],
            "+27812345678",
            "222",
            id="unparseable_number_skipped",
        ),
    ],
)
@patch("apps.service_providers.messaging_service.httpx.get")
def test_meta_cloud_api_get_phone_number_id(mock_get, meta_cloud_api_service, api_data, lookup_number, expected_id):
    mock_get.return_value = _mock_phone_numbers_response(api_data)
    assert meta_cloud_api_service.resolve_number(lookup_number) == expected_id


class TestTurnWhatsappMessageParsing:
    """Tests for TurnWhatsappMessage content type parsing."""

    def test_audio_message_type_maps_to_voice(self):
        """WhatsApp sends 'audio' as the message type, which should map to MESSAGE_TYPES.VOICE."""
        message = TurnWhatsappMessage.parse(turnio_messages.audio_message())
        assert message.content_type == MESSAGE_TYPES.VOICE
        assert message.media_id == "1215194677037265"

    def test_voice_message_type_maps_to_voice(self):
        """Turn.io sends 'voice' as the message type for voice notes."""
        message_data = {
            "contacts": [{"wa_id": "27826419977", "profile": {"name": "Test"}}],
            "messages": [
                {
                    "from": "27826419977",
                    "id": "wamid.test",
                    "timestamp": "1773300527",
                    "type": "voice",
                    "voice": {
                        "mime_type": "audio/ogg; codecs=opus",
                        "id": "voice-media-id",
                    },
                }
            ],
        }
        message = TurnWhatsappMessage.parse(message_data)
        assert message.content_type == MESSAGE_TYPES.VOICE

    def test_text_message_type_maps_to_text(self):
        message_data = {
            "contacts": [{"wa_id": "27826419977", "profile": {"name": "Test"}}],
            "messages": [
                {
                    "from": "27826419977",
                    "id": "wamid.test",
                    "timestamp": "1773300527",
                    "type": "text",
                    "text": {"body": "Hello"},
                }
            ],
        }
        message = TurnWhatsappMessage.parse(message_data)
        assert message.content_type == MESSAGE_TYPES.TEXT


class TestMetaCloudAPIServiceAudio:
    """Tests for MetaCloudAPIService audio message support."""

    def test_voice_replies_supported(self, meta_cloud_api_service):
        assert meta_cloud_api_service.voice_replies_supported is True

    @patch("apps.service_providers.messaging_service.httpx.get")
    def test_get_message_audio_fetches_and_converts(self, mock_get, meta_cloud_api_service):
        """get_message_audio should:
        1. GET the media URL from Meta's API using the media_id
        2. Download the binary audio from that URL
        3. Cache the media data on the message
        4. Convert the audio to WAV format
        """
        # Step 1 response: Meta API returns the media download URL
        media_url_response = httpx.Response(
            200,
            json={"url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=123"},
            request=httpx.Request("GET", "https://graph.facebook.com/v25.0/123"),
        )
        # Step 2 response: downloading the actual audio binary
        audio_bytes = b"fake-audio-content"
        audio_download_response = httpx.Response(
            200,
            content=audio_bytes,
            headers={"Content-Type": "audio/ogg"},
            request=httpx.Request("GET", "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=123"),
        )
        mock_get.side_effect = [media_url_response, audio_download_response]

        message = MetaCloudAPIMessage(
            participant_id="27826419977",
            message_text="",
            content_type="voice",
            media_id="123",
            content_type_unparsed="voice",
        )

        with patch("apps.service_providers.messaging_service.audio.convert_audio") as mock_convert:
            mock_convert.return_value = BytesIO(b"converted-wav")
            meta_cloud_api_service.get_message_audio(message)

        # Verify the two HTTP calls were made correctly
        assert mock_get.call_count == 2
        # First call: get media URL
        first_call = mock_get.call_args_list[0]
        assert "123" in first_call.args[0]  # media_id in URL
        # Second call: download audio binary
        second_call = mock_get.call_args_list[1]
        assert urlparse(second_call.args[0]).hostname == "lookaside.fbsbx.com"

        # Verify media data was cached on the message
        assert message.cached_media_data is not None
        assert message.cached_media_data.content_type == "audio/ogg"

        # Verify audio was converted
        mock_convert.assert_called_once()
        call_kwargs = mock_convert.call_args
        assert call_kwargs.kwargs["target_format"] == "wav"
        assert call_kwargs.kwargs["source_format"] == "ogg"

    @patch("apps.service_providers.messaging_service.httpx.get")
    def test_get_message_audio_raises_on_non_audio(self, mock_get, meta_cloud_api_service):
        """Should raise AudioConversionError if the downloaded content is not audio."""
        media_url_response = httpx.Response(
            200,
            json={"url": "https://example.com/media"},
            request=httpx.Request("GET", "https://graph.facebook.com/v25.0/456"),
        )
        non_audio_response = httpx.Response(
            200,
            content=b"not-audio",
            headers={"Content-Type": "image/jpeg"},
            request=httpx.Request("GET", "https://example.com/media"),
        )
        mock_get.side_effect = [media_url_response, non_audio_response]

        message = MetaCloudAPIMessage(
            participant_id="27826419977",
            message_text="",
            content_type="voice",
            media_id="456",
            content_type_unparsed="voice",
        )

        with pytest.raises(AudioConversionError):
            meta_cloud_api_service.get_message_audio(message)

    @patch("apps.service_providers.messaging_service.httpx.get")
    def test_get_message_audio_raises_on_http_error(self, mock_get, meta_cloud_api_service):
        """Should raise AudioConversionError if the media download fails."""
        media_url_response = httpx.Response(
            200,
            json={"url": "https://example.com/media"},
            request=httpx.Request("GET", "https://graph.facebook.com/v25.0/789"),
        )
        error_response = httpx.Response(
            404,
            request=httpx.Request("GET", "https://example.com/media"),
        )
        mock_get.side_effect = [media_url_response, error_response]

        message = MetaCloudAPIMessage(
            participant_id="27826419977",
            message_text="",
            content_type="voice",
            media_id="789",
            content_type_unparsed="voice",
        )

        with pytest.raises(AudioConversionError):
            meta_cloud_api_service.get_message_audio(message)

    @patch("apps.service_providers.messaging_service.httpx.get")
    def test_get_message_audio_raises_on_media_url_http_error(self, mock_get, meta_cloud_api_service):
        """Should raise AudioConversionError if resolving the media URL fails."""
        error_response = httpx.Response(
            404,
            request=httpx.Request("GET", "https://graph.facebook.com/v25.0/bad_id"),
        )
        mock_get.side_effect = [error_response]

        message = MetaCloudAPIMessage(
            participant_id="27826419977",
            message_text="",
            content_type="voice",
            media_id="bad_id",
            content_type_unparsed="voice",
        )

        with pytest.raises(AudioConversionError, match="Unable to resolve media URL"):
            meta_cloud_api_service.get_message_audio(message)

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_voice_message(self, mock_post, meta_cloud_api_service):
        """send_voice_message should:
        1. Upload audio to Meta's media API
        2. Send a message referencing the uploaded media ID
        """
        # Upload response returns the media ID
        upload_response = httpx.Response(
            200,
            json={"id": "media_id_abc"},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/media"),
        )
        # Send message response
        send_response = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.xyz"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        mock_post.side_effect = [upload_response, send_response]

        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        synthetic_voice.get_audio_bytes.return_value = b"fake-ogg-audio"

        meta_cloud_api_service.send_voice_message(
            synthetic_voice=synthetic_voice,
            from_="phone123",
            to="27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now(),
        )

        # Should have called get_audio_bytes with ogg format and opus codec
        synthetic_voice.get_audio_bytes.assert_called_once_with(format="ogg", codec="libopus")

        # Two POST calls: upload media, send message
        assert mock_post.call_count == 2

        # First call: upload media
        upload_call = mock_post.call_args_list[0]
        assert "/media" in upload_call.args[0]

        # Second call: send message with audio type
        send_call = mock_post.call_args_list[1]
        assert "/messages" in send_call.args[0]
        send_data = send_call.kwargs["json"]
        assert send_data["type"] == "audio"
        assert send_data["audio"]["id"] == "media_id_abc"
        assert send_data["to"] == "27826419977"


class TestMetaCloudAPIServiceWindow:
    """Tests for MetaCloudAPIService service window logic."""

    def _make_service(self, has_template=False):
        return MetaCloudAPIService(
            access_token="test_token",
            business_id="123456",
            has_template_message_configured=has_template,
        )

    def test_none_last_activity_is_outside_window(self):
        service = self._make_service()
        assert service._is_within_service_window(None) is False

    def test_23_hours_ago_is_within_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=23)
        assert service._is_within_service_window(last_activity) is True

    def test_25_hours_ago_is_outside_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=25)
        assert service._is_within_service_window(last_activity) is False

    def test_exactly_24_hours_ago_is_outside_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=24)
        assert service._is_within_service_window(last_activity) is False

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_short_message(self, mock_post):
        """Template message with text under the char limit sends one request."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_template_message(
            message="Hello, any update?",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )
        mock_post.assert_called_once()
        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"
        assert data["template"]["name"] == "new_bot_message"
        assert data["template"]["language"]["code"] == "en"
        body_params = data["template"]["components"][0]["parameters"]
        assert len(body_params) == 1
        assert body_params[0]["parameter_name"] == "bot_message"
        assert body_params[0]["text"] == "Hello, any update?"

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_splits_long_message(self, mock_post):
        """Messages exceeding 974 chars are split into multiple template messages."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        long_message = "A" * 1500
        service.send_template_message(
            message=long_message,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )
        assert mock_post.call_count == 2
        first_text = mock_post.call_args_list[0].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert first_text == "A" * 971 + "..."
        assert len(first_text) == 974
        second_text = mock_post.call_args_list[1].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert second_text == "A" * 529

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_exactly_at_limit(self, mock_post):
        """Message exactly at 974 chars should send as one message without ellipsis."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_template_message(
            message="A" * 974,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )
        mock_post.assert_called_once()
        text = mock_post.call_args.kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert text == "A" * 974

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_multiple_splits(self, mock_post):
        """Very long messages produce 3+ template messages."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_template_message(
            message="B" * 2500,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )
        assert mock_post.call_count == 3
        for i in range(2):
            text = mock_post.call_args_list[i].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
            assert text.endswith("...")
            assert len(text) == 974
        last_text = mock_post.call_args_list[2].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert not last_text.endswith("...")
        assert last_text == "B" * (2500 - 971 * 2)

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_within_window_sends_normal(self, mock_post):
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "text"

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_outside_window_with_template_sends_template(self, mock_post):
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=25),
        )
        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"

    def test_send_text_outside_window_without_template_raises(self):
        service = self._make_service(has_template=False)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_text_message(
                message="Hello",
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=timezone.now() - timedelta(hours=25),
            )

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_none_activity_with_template_sends_template(self, mock_post):
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=None,
        )
        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"

    def test_send_text_none_activity_without_template_raises(self):
        service = self._make_service(has_template=False)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_text_message(
                message="Hello",
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=None,
            )

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_voice_within_window_sends_normal(self, mock_post):
        upload_response = httpx.Response(
            200,
            json={"id": "media_id_abc"},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/media"),
        )
        send_response = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.xyz"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        mock_post.side_effect = [upload_response, send_response]
        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        synthetic_voice.get_audio_bytes.return_value = b"fake-ogg-audio"
        service.send_voice_message(
            synthetic_voice=synthetic_voice,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=timezone.now() - timedelta(hours=1),
        )
        assert mock_post.call_count == 2

    def test_send_voice_outside_window_raises_regardless_of_template(self):
        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_voice_message(
                synthetic_voice=synthetic_voice,
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=timezone.now() - timedelta(hours=25),
            )
        synthetic_voice.get_audio_bytes.assert_not_called()

    def test_send_voice_none_activity_raises(self):
        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_voice_message(
                synthetic_voice=synthetic_voice,
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=None,
            )
        synthetic_voice.get_audio_bytes.assert_not_called()


def _test_messaging_provider(team, provider_type: MessagingProviderType, data):
    form = provider_type.form_cls(team, data=data)
    assert form.is_valid()
    MessagingProvider.objects.create(
        team=team,
        name=f"{provider_type} Test Provider",
        type=provider_type,
        config=form.cleaned_data,
    )
