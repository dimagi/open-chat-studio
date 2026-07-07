"""Tests for WhatsApp inbound attachments (image, document) across Twilio, TurnIO, and Meta Cloud API.

Stage-level tests for ``WhatsappAttachmentHydrationStage`` live in
``test_whatsapp_attachment_hydration_stage.py``.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.channels.const import MESSAGE_TYPES
from apps.channels.datamodels import TwilioMessage, WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.chat.models import Chat, ChatAttachment, ChatMessage
from apps.files.models import File, FilePurpose
from apps.service_providers.messaging_service import MetaCloudAPIService, TurnIOService, TwilioService
from apps.utils.factories.channels import ExperimentChannelFactory

from ._whatsapp_attachment_helpers import (
    assert_file_resolves_via_download_file_join,
    setup_inbound_bot_response,
)
from .message_examples import meta_cloud_api_messages, turnio_messages, twilio_messages

# ===========================================================================
# IMAGE ATTACHMENTS
# ===========================================================================


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

    def test_parse_image_message_preserves_media_id(self):
        """Meta image media_id is required to download via Media API."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.media_id == "image-media-id-456"

    def test_parse_image_message_preserves_whatsapp_message_id(self):
        """whatsapp_message_id should still be captured for the typing indicator."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.whatsapp_message_id == "wamid.img001"


# ---------------------------------------------------------------------------
# download_message_media() — provider-level media fetch (image)
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


# ===========================================================================
# DOCUMENT ATTACHMENTS
# ===========================================================================


# ---------------------------------------------------------------------------
# Parser tests — document-specific behaviour (real MIME, filename)
#
# Twilio's MMS parser doesn't distinguish image from document, so the Twilio
# image parser tests above already cover the document path. Only the TurnIO
# and Meta parsers diverge for documents — they expose a real MIME type and a
# filename — so only those provider-specific assertions live here.
# ---------------------------------------------------------------------------


class TestTurnioDocumentParsing:
    def test_parse_document_message_preserves_real_mime_type(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(mime_type="application/pdf"))
        assert parsed.attachment_mime_type == "application/pdf"

    def test_parse_document_message_preserves_filename(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(filename="report.pdf"))
        assert parsed.attachment_filename == "report.pdf"


class TestMetaCloudApiDocumentParsing:
    def test_parse_document_message_preserves_real_mime_type(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(mime_type="application/pdf"))
        assert parsed.attachment_mime_type == "application/pdf"

    def test_parse_document_message_preserves_filename(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(filename="invoice.pdf"))
        assert parsed.attachment_filename == "invoice.pdf"


# ---------------------------------------------------------------------------
# get_inbound_media() — provider-level document downloads
# ---------------------------------------------------------------------------


class TestTwilioInboundMediaDocument:
    def test_get_inbound_media_returns_document_bytes(self):
        """Twilio's MIME-driven download path also serves document attachments."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage.parse(twilio_messages.Whatsapp.document_message())

        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake"
        mock_response.headers = {"Content-Type": "application/pdf"}

        with patch("httpx.get", return_value=mock_response):
            result = service.get_inbound_media(message)

        assert result == (b"%PDF-1.4 fake", "application/pdf")

    def test_get_inbound_media_skips_audio(self):
        """Voice/audio is routed via get_message_audio and must not be returned here."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage.parse(twilio_messages.Whatsapp.audio_message())
        assert service.get_inbound_media(message) is None


class TestTurnioInboundMediaDocument:
    def test_get_inbound_media_returns_document_bytes(self):
        """TurnIO document message: media_url is preferred, bytes flow through."""
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage.parse(turnio_messages.document_message())
        assert message.attachment_mime_type == "application/pdf"

        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake"
        mock_response.headers = {"Content-Type": "application/pdf"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.get_inbound_media(message)

        mock_get.assert_called_once()
        assert mock_get.call_args.args[0] == "https://media.turn.io/turn-document-media-id-789"
        assert data == b"%PDF-1.4 fake"
        assert content_type == "application/pdf"

    @patch("apps.service_providers.messaging_service.TurnIOService.client")
    def test_get_inbound_media_falls_back_to_media_id(self, mock_client):
        """Without media_url, document downloads still fall back to the SDK media_id endpoint."""
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage(
            participant_id="x",
            message_text="",
            media_id="legacy-doc-id",
            attachment_mime_type="application/pdf",
        )

        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake"
        mock_response.headers = {"Content-Type": "application/pdf"}
        mock_client.media.get_media.return_value = mock_response

        data, content_type = service.get_inbound_media(message)

        mock_client.media.get_media.assert_called_once_with("legacy-doc-id")
        assert data == b"%PDF-1.4 fake"
        assert content_type == "application/pdf"

    def test_get_inbound_media_skips_audio(self):
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage(
            participant_id="x",
            message_text="",
            media_id="m",
            attachment_mime_type="audio",
        )
        assert service.get_inbound_media(message) is None


class TestMetaCloudApiInboundMediaDocument:
    def test_get_inbound_media_returns_document_bytes(self):
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value())
        assert message.attachment_mime_type == "application/pdf"

        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 fake"
        mock_response.headers = {"Content-Type": "application/pdf"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.get_inbound_media(message)

        mock_get.assert_called_once()
        assert mock_get.call_args.args[0] == "https://cdn.meta.example.com/document-media-id-789"
        assert data == b"%PDF-1.4 fake"
        assert content_type == "application/pdf"

    def test_get_inbound_media_falls_back_to_media_id_resolution(self):
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage(
            participant_id="x",
            message_text="",
            media_id="legacy-doc-id",
            attachment_mime_type="application/pdf",
        )

        url_response = MagicMock()
        url_response.json.return_value = {"url": "https://cdn.meta.example.com/doc-resolved"}
        url_response.raise_for_status = MagicMock()

        media_response = MagicMock()
        media_response.content = b"%PDF-1.4 fake"
        media_response.headers = {"Content-Type": "application/pdf"}
        media_response.raise_for_status = MagicMock()

        with patch("httpx.get", side_effect=[url_response, media_response]):
            data, content_type = service.get_inbound_media(message)

        assert data == b"%PDF-1.4 fake"
        assert content_type == "application/pdf"

    def test_get_inbound_media_skips_audio(self):
        service = MetaCloudAPIService(business_id="BIZ", access_token="TOKEN")
        message = WhatsAppMessage(
            participant_id="x",
            message_text="",
            media_id="m",
            attachment_mime_type="audio",
        )
        assert service.get_inbound_media(message) is None


# ---------------------------------------------------------------------------
# End-to-end task integration — document attachments
# ---------------------------------------------------------------------------


class TestTwilioInboundDocumentTask:
    @pytest.mark.django_db()
    @patch("apps.channels.tasks.validate_twillio_request", Mock())
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TwilioService.download_message_media")
    def test_inbound_document_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        twilio_provider,
    ):
        download_media_mock.return_value = (b"%PDF-1.4 fake", "application/pdf")
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=twilio_provider,
            experiment__team=twilio_provider.team,
            extra_data={"number": "+14155238886"},
        )
        experiment = channel.experiment
        bot_process_input.return_value = setup_inbound_bot_response(experiment)

        with patch("apps.service_providers.messaging_service.TwilioService.client"):
            handle_twilio_message(message_data=twilio_messages.Whatsapp.document_message())

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert file.content_type == "application/pdf"
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)


class TestTurnioInboundDocumentTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TurnIOService.download_message_media")
    def test_inbound_document_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        turnio_whatsapp_channel,
    ):
        download_media_mock.return_value = (b"%PDF-1.4 fake", "application/pdf")
        experiment = turnio_whatsapp_channel.experiment
        bot_process_input.return_value = setup_inbound_bot_response(experiment)

        handle_turn_message(
            experiment_id=turnio_whatsapp_channel.experiment.public_id,
            message_data=turnio_messages.document_message(filename="report.pdf"),
        )

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert file.name == "report.pdf"
        assert file.content_type == "application/pdf"
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)


class TestMetaCloudApiInboundDocumentTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.download_message_media")
    def test_inbound_document_creates_file_and_links_to_chat(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        meta_cloud_api_whatsapp_channel,
    ):
        download_media_mock.return_value = (b"%PDF-1.4 fake", "application/pdf")
        experiment = meta_cloud_api_whatsapp_channel.experiment
        bot_process_input.return_value = setup_inbound_bot_response(experiment)

        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=meta_cloud_api_messages.document_message_value(filename="invoice.pdf"),
        )

        file = File.objects.get(purpose=FilePurpose.MESSAGE_MEDIA)
        assert file.name == "invoice.pdf"
        assert file.content_type == "application/pdf"
        assert ChatAttachment.objects.filter(files=file, tool_type="ocs_attachments").exists()
        assert_file_resolves_via_download_file_join(file, experiment.team.slug)
