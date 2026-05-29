"""Tests for ``WhatsappAttachmentHydrationStage.process()`` across image and document attachments."""

import pytest

from apps.channels.channels_v2.whatsapp_channel import WhatsappAttachmentHydrationStage
from apps.channels.datamodels import TwilioMessage, WhatsAppMessage
from apps.files.models import File, FilePurpose

from ._whatsapp_attachment_helpers import make_stage_context
from .channels.conftest import make_context
from .message_examples import meta_cloud_api_messages, turnio_messages, twilio_messages


class TestWhatsappAttachmentHydrationStageProcess:
    """Image-attachment behaviour of ``WhatsappAttachmentHydrationStage.process()``."""

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


class TestWhatsappAttachmentHydrationStageDocuments:
    """Document-attachment behaviour of ``WhatsappAttachmentHydrationStage.process()``."""

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


class TestWhatsappAttachmentHydrationStageShouldRun:
    """``should_run`` only fires when the message actually references downloadable media.

    ``WhatsAppMessage.parse`` sets ``attachment_mime_type`` on every parsed message
    (including text), so the gate must also require ``media_url`` or ``media_id``."""

    def setup_method(self):
        self.stage = WhatsappAttachmentHydrationStage()

    def test_false_for_meta_text_message(self):
        message = WhatsAppMessage.parse(meta_cloud_api_messages.text_message_value())
        ctx = make_context(message=message, experiment_session=object())

        assert self.stage.should_run(ctx) is False

    def test_false_for_turnio_text_message(self):
        message = WhatsAppMessage.parse(turnio_messages.text_message())
        ctx = make_context(message=message, experiment_session=object())

        assert self.stage.should_run(ctx) is False

    def test_true_for_meta_image_message(self):
        message = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        ctx = make_context(message=message, experiment_session=object())

        assert self.stage.should_run(ctx) is True
