"""Tests for WhatsApp inbound document attachments across Twilio, TurnIO, and Meta Cloud API."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.channels.channels_v2.stages.core import WhatsappAttachmentHydrationStage
from apps.channels.datamodels import TwilioMessage, WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import ChatAttachment
from apps.files.models import File, FilePurpose
from apps.service_providers.messaging_service import MetaCloudAPIService, TurnIOService, TwilioService
from apps.utils.factories.channels import ExperimentChannelFactory

from ._whatsapp_attachment_helpers import (
    assert_file_resolves_via_download_file_join,
    make_stage_context,
    setup_inbound_bot_response,
)
from .message_examples import meta_cloud_api_messages, turnio_messages, twilio_messages

# ---------------------------------------------------------------------------
# Parser tests — document messages are classified as TEXT with real MIME type
# ---------------------------------------------------------------------------


class TestTwilioDocumentParsing:
    def test_parse_document_message_content_type_is_text(self):
        """Twilio document MMS should be classified as TEXT so it passes MessageTypeValidationStage."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.document_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_document_message_preserves_media_url(self):
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.document_message())
        assert parsed.media_url == "http://example.com/media"

    def test_parse_document_message_preserves_mime_type(self):
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.document_message(mime_type="application/pdf"))
        assert parsed.attachment_mime_type == "application/pdf"

    def test_parse_document_message_preserves_body_text(self):
        raw = twilio_messages.Whatsapp.document_message()
        raw["Body"] = "Please see attached"
        parsed = TwilioMessage.parse(raw)
        assert parsed.message_text == "Please see attached"


class TestTurnioDocumentParsing:
    def test_parse_document_message_content_type_is_text(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_document_message_uses_caption_as_body(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(caption="Here's the report"))
        assert parsed.message_text == "Here's the report"

    def test_parse_document_message_empty_caption_gives_empty_body(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(caption=""))
        assert parsed.message_text == ""

    def test_parse_document_message_preserves_real_mime_type(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(mime_type="application/pdf"))
        assert parsed.attachment_mime_type == "application/pdf"

    def test_parse_document_message_preserves_filename(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message(filename="report.pdf"))
        assert parsed.attachment_filename == "report.pdf"

    def test_parse_document_message_preserves_media_url(self):
        parsed = WhatsAppMessage.parse(turnio_messages.document_message())
        assert parsed.media_url == "https://media.turn.io/turn-document-media-id-789"


class TestMetaCloudApiDocumentParsing:
    def test_parse_document_message_content_type_is_text(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_document_message_uses_caption_as_body(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(caption="See attached"))
        assert parsed.message_text == "See attached"

    def test_parse_document_message_empty_caption_gives_empty_body(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value_no_caption())
        assert parsed.message_text == ""

    def test_parse_document_message_preserves_real_mime_type(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(mime_type="application/pdf"))
        assert parsed.attachment_mime_type == "application/pdf"

    def test_parse_document_message_preserves_filename(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(filename="invoice.pdf"))
        assert parsed.attachment_filename == "invoice.pdf"

    def test_parse_document_message_preserves_media_url(self):
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value())
        assert parsed.media_url == "https://cdn.meta.example.com/document-media-id-789"


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
# WhatsappAttachmentHydrationStage.process() — document path per provider
# ---------------------------------------------------------------------------


class TestWhatsappAttachmentHydrationStageDocuments:
    def setup_method(self):
        self.stage = WhatsappAttachmentHydrationStage()

    @pytest.mark.django_db()
    def test_twilio_document_creates_file_with_pdf_content_type(self, twilio_provider):
        message = TwilioMessage.parse(twilio_messages.Whatsapp.document_message())
        ctx, _ = make_stage_context(message, twilio_provider, (b"%PDF-1.4 fake", "application/pdf"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA
        assert file_obj.content_type == "application/pdf"

    @pytest.mark.django_db()
    def test_turnio_document_persists_provider_filename(self, turn_io_provider):
        message = WhatsAppMessage.parse(turnio_messages.document_message(filename="report.pdf"))
        ctx, _ = make_stage_context(message, turn_io_provider, (b"%PDF-1.4 fake", "application/pdf"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.name == "report.pdf"
        assert file_obj.content_type == "application/pdf"

    @pytest.mark.django_db()
    def test_meta_document_persists_provider_filename(self, meta_cloud_api_provider):
        message = WhatsAppMessage.parse(meta_cloud_api_messages.document_message_value(filename="invoice.pdf"))
        ctx, _ = make_stage_context(message, meta_cloud_api_provider, (b"%PDF-1.4 fake", "application/pdf"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.name == "invoice.pdf"
        assert file_obj.content_type == "application/pdf"


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
