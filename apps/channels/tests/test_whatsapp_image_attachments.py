"""Tests for WhatsApp inbound image attachments across Twilio, TurnIO, and Meta Cloud API."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.channels.channels_v2.stages.core import WhatsappAttachmentHydrationStage
from apps.channels.datamodels import TwilioMessage, WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat, ChatAttachment, ChatMessage
from apps.files.models import File, FilePurpose
from apps.service_providers.messaging_service import MetaCloudAPIService, TurnIOService, TwilioService
from apps.utils.factories.channels import ExperimentChannelFactory

from ._whatsapp_attachment_helpers import (
    assert_file_resolves_via_download_file_join,
    make_stage_context,
)
from .message_examples import meta_cloud_api_messages, turnio_messages, twilio_messages

# ---------------------------------------------------------------------------
# Parser tests — image messages are classified as TEXT
# ---------------------------------------------------------------------------


class TestTwilioImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """Twilio image MMS should be classified as TEXT so it passes MessageTypeValidationStage."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_preserves_media_url(self):
        """Twilio image MMS should preserve the media URL for download."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.media_url == "http://example.com/media"

    def test_parse_image_message_preserves_body_text(self):
        """Twilio image messages carry the text body (MMS caption) unchanged."""
        raw = twilio_messages.Whatsapp.image_message()
        raw["Body"] = "MMS caption"
        parsed = TwilioMessage.parse(raw)
        assert parsed.message_text == "MMS caption"

    def test_parse_image_attachment_mime_type_preserved(self):
        """attachment_mime_type should hold the original MIME string."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.attachment_mime_type == "image/png"


class TestTurnioImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """TurnIO image message should be classified as TEXT."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_uses_caption_as_body(self):
        """TurnIO image caption becomes message_text."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message(caption="Look at this!"))
        assert parsed.message_text == "Look at this!"

    def test_parse_image_message_empty_caption_gives_empty_body(self):
        """An image with no caption should produce empty message_text."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message(caption=""))
        assert parsed.message_text == ""

    def test_parse_image_message_preserves_media_id(self):
        """TurnIO image media_id is required for download."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message())
        assert parsed.media_id == "turn-image-media-id-789"


class TestMetaCloudApiImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """Meta Cloud API image message should be classified as TEXT."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_uses_caption_as_body(self):
        """Meta image caption becomes message_text."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value(caption="Check this out"))
        assert parsed.message_text == "Check this out"

    def test_parse_image_message_empty_caption_gives_empty_body(self):
        """An image with no caption should produce empty message_text."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value_no_caption())
        assert parsed.message_text == ""

    def test_parse_image_message_preserves_media_id(self):
        """Meta image media_id is required to download via Media API."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.media_id == "image-media-id-456"

    def test_parse_image_message_preserves_whatsapp_message_id(self):
        """whatsapp_message_id should still be captured for the typing indicator."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.whatsapp_message_id == "wamid.img001"


# ---------------------------------------------------------------------------
# download_message_media() — provider-level media fetch
# ---------------------------------------------------------------------------


class TestTwilioDownloadMedia:
    def test_download_image_returns_bytes_and_content_type(self):
        """TwilioService.download_message_media() returns (bytes, content_type) for an image."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())

        mock_response = MagicMock()
        mock_response.content = b"\x89PNG"
        mock_response.headers = {"Content-Type": "image/png"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.download_message_media(message)

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args.args[0] == "http://example.com/media"
        assert call_args.kwargs["auth"] == ("SID", "TOKEN")
        assert call_args.kwargs["follow_redirects"] is True
        assert call_args.kwargs["timeout"] > 0
        assert data == b"\x89PNG"
        assert content_type == "image/png"

    def test_download_normalizes_parameterized_content_type(self):
        """Provider-supplied Content-Type with parameters/casing is normalized."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())

        mock_response = MagicMock()
        mock_response.content = b"\x89PNG"
        mock_response.headers = {"Content-Type": "Image/PNG; charset=binary"}

        with patch("httpx.get", return_value=mock_response):
            _, content_type = service.download_message_media(message)

        assert content_type == "image/png"

    def test_download_raises_when_media_url_missing(self):
        """download_message_media raises a clear error when media_url is None."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage(participant_id="x", message_text="", to="y", platform=ChannelPlatform.WHATSAPP)

        with pytest.raises(ValueError, match="media_url is empty"):
            service.download_message_media(message)


class TestTurnioDownloadMedia:
    def test_download_image_prefers_media_url(self):
        """When the webhook payload includes a url, TurnIOService fetches it directly."""
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage.parse(turnio_messages.image_message())
        assert message.media_url == "https://media.turn.io/turn-image-media-id-789"

        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8"
        mock_response.headers = {"Content-Type": "image/jpeg"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.download_message_media(message)

        mock_get.assert_called_once()
        assert mock_get.call_args.args[0] == "https://media.turn.io/turn-image-media-id-789"
        assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer TOKEN"}
        assert data == b"\xff\xd8"
        assert content_type == "image/jpeg"

    @patch("apps.service_providers.messaging_service.TurnIOService.client")
    def test_download_falls_back_to_media_id_via_sdk(self, mock_client):
        """Without a media_url, TurnIOService falls back to the SDK media_id endpoint."""
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage(participant_id="x", message_text="", media_id="legacy-id")

        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8"
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_client.media.get_media.return_value = mock_response

        data, content_type = service.download_message_media(message)

        mock_client.media.get_media.assert_called_once_with("legacy-id")
        assert data == b"\xff\xd8"
        assert content_type == "image/jpeg"

    def test_download_raises_when_no_url_or_id(self):
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage(participant_id="x", message_text="")
        with pytest.raises(ValueError, match="media_url and media_id are empty"):
            service.download_message_media(message)


class TestMetaCloudApiDownloadMedia:
    def test_download_image_prefers_media_url(self):
        """When the Meta payload includes a url, the service skips the _get_media_url indirection."""
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert message.media_url == "https://cdn.meta.example.com/image-media-id-456"

        mock_response = MagicMock()
        mock_response.content = b"\x89PNG"
        mock_response.headers = {"Content-Type": "image/png"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.download_message_media(message)

        mock_get.assert_called_once()
        assert mock_get.call_args.args[0] == "https://cdn.meta.example.com/image-media-id-456"
        assert data == b"\x89PNG"
        assert content_type == "image/png"

    def test_download_falls_back_to_media_id_resolution(self):
        """Without a media_url, the service resolves the URL from media_id then fetches it."""
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage(participant_id="x", message_text="", media_id="legacy-id")

        url_response = MagicMock()
        url_response.json.return_value = {"url": "https://cdn.meta.example.com/image123"}
        url_response.raise_for_status = MagicMock()

        media_response = MagicMock()
        media_response.content = b"\x89PNG"
        media_response.headers = {"Content-Type": "image/png"}
        media_response.raise_for_status = MagicMock()

        with patch("httpx.get", side_effect=[url_response, media_response]):
            data, content_type = service.download_message_media(message)

        assert data == b"\x89PNG"
        assert content_type == "image/png"

    def test_download_raises_when_no_url_or_id(self):
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage(participant_id="x", message_text="")
        with pytest.raises(ValueError, match="media_url and media_id are empty"):
            service.download_message_media(message)


# ---------------------------------------------------------------------------
# WhatsappAttachmentHydrationStage.process() — File creation per provider
# ---------------------------------------------------------------------------


class TestWhatsappAttachmentHydrationStageProcess:
    def setup_method(self):
        self.stage = WhatsappAttachmentHydrationStage()

    @pytest.mark.django_db()
    def test_twilio_image_creates_file_record(self, twilio_provider):
        """Twilio image message: one MESSAGE_MEDIA File persisted and attached."""
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        ctx, _ = make_stage_context(message, twilio_provider, (b"\x89PNG\r\n\x1a\n", "image/png"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA
        assert file_obj.content_type == "image/png"

    @pytest.mark.django_db()
    def test_turnio_image_creates_file_record(self, turn_io_provider):
        """TurnIO image message: one MESSAGE_MEDIA File persisted and attached."""
        message = WhatsAppMessage.parse(turnio_messages.image_message())
        ctx, _ = make_stage_context(message, turn_io_provider, (b"\xff\xd8\xff\xe0", "image/jpeg"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA

    @pytest.mark.django_db()
    def test_meta_image_creates_file_record(self, meta_cloud_api_provider):
        """Meta image message: one MESSAGE_MEDIA File persisted and attached."""
        message = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        ctx, _ = make_stage_context(message, meta_cloud_api_provider, (b"\x89PNG\r\n\x1a\n", "image/png"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA

    @pytest.mark.django_db()
    def test_non_image_message_creates_no_files(self, twilio_provider):
        """Text-only messages (service returns None) create no File rows."""
        message = TwilioMessage.parse(twilio_messages.Whatsapp.text_message())
        ctx, _ = make_stage_context(message, twilio_provider, None)

        self.stage.process(ctx)

        assert message.attachments == []
        assert File.objects.filter(purpose=FilePurpose.MESSAGE_MEDIA).count() == 0


# ---------------------------------------------------------------------------
# End-to-end task integration — image attachments
# ---------------------------------------------------------------------------


class TestTwilioInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.channels.tasks.validate_twillio_request", Mock())
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TwilioService.download_message_media")
    def test_inbound_image_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        twilio_provider,
    ):
        """handle_twilio_message with an image payload: File created and linked to chat
        via ChatAttachment so the download_file view's join resolves."""
        download_media_mock.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=twilio_provider,
            experiment__team=twilio_provider.team,
            extra_data={"number": "+14155238886"},
        )
        experiment = channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        with patch("apps.service_providers.messaging_service.TwilioService.client"):
            handle_twilio_message(message_data=twilio_messages.Whatsapp.image_message())

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)


class TestTurnioInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TurnIOService.download_message_media")
    def test_inbound_image_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        turnio_whatsapp_channel,
    ):
        """handle_turn_message with an image payload: File created and linked via ChatAttachment."""
        download_media_mock.return_value = (b"\xff\xd8\xff\xe0", "image/jpeg")
        experiment = turnio_whatsapp_channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        handle_turn_message(
            experiment_id=turnio_whatsapp_channel.experiment.public_id,
            message_data=turnio_messages.image_message(),
        )

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)


class TestMetaCloudApiInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.download_message_media")
    def test_inbound_image_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        meta_cloud_api_whatsapp_channel,
    ):
        """handle_meta_cloud_api_message with an image payload: File created and linked via ChatAttachment."""
        download_media_mock.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")
        experiment = meta_cloud_api_whatsapp_channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=meta_cloud_api_messages.image_message_value(),
        )

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)
