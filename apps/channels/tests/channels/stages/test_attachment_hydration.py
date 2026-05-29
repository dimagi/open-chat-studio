from unittest.mock import MagicMock

import pytest
from django.urls import reverse

from apps.channels.channels_v2.stages.core import AttachmentHydrationStage, WhatsappAttachmentHydrationStage
from apps.channels.datamodels import Attachment, BaseMessage
from apps.channels.tests.channels.conftest import make_context
from apps.chat.models import ChatAttachment
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


class TestAttachmentHydrationStage:
    def setup_method(self):
        self.stage = AttachmentHydrationStage()

    def test_skips_when_no_file_ids(self):
        msg = BaseMessage(participant_id="u1", message_text="hi")
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    def test_skips_when_session_missing(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[1])
        ctx = make_context(message=msg, experiment_session=None)
        assert self.stage.should_run(ctx) is False

    def test_skips_when_attachments_already_populated(self):
        msg = BaseMessage(
            participant_id="u1",
            message_text="hi",
            attachment_file_ids=[1],
            attachments=[Attachment(file_id=1, type="ocs_attachments", name="x", size=1, download_link="")],
        )
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    @pytest.mark.django_db()
    def test_hydrates_attachments_from_file_ids(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        file_a = FileFactory(team=experiment.team, name="a.pdf", content_type="application/pdf")
        file_b = FileFactory(team=experiment.team, name="b.csv", content_type="text/csv")

        msg = BaseMessage(
            participant_id="u1",
            message_text="hi",
            attachment_file_ids=[file_a.id, file_b.id],
        )
        ctx = make_context(
            message=msg,
            experiment=experiment,
            experiment_session=session,
        )

        assert self.stage.should_run(ctx) is True
        self.stage.process(ctx)

        assert len(msg.attachments) == 2
        names = {a.name for a in msg.attachments}
        assert names == {"a.pdf", "b.csv"}
        for att in msg.attachments:
            assert att.type == "ocs_attachments"
            # download_link must reference the real session, not 0
            assert str(session.id) in att.download_link

    @pytest.mark.django_db()
    def test_links_files_to_chat_via_chat_attachment(self):
        """Hydrated files must be linked to the session's Chat via ChatAttachment
        so the experiments:download_file view's join succeeds when an LLM provider
        fetches the download_link."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        file_a = FileFactory(team=experiment.team, name="a.pdf", content_type="application/pdf")
        file_b = FileFactory(team=experiment.team, name="b.csv", content_type="text/csv")

        msg = BaseMessage(
            participant_id="u1",
            message_text="hi",
            attachment_file_ids=[file_a.id, file_b.id],
        )
        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)

        self.stage.process(ctx)

        chat_attachment = ChatAttachment.objects.get(chat=session.chat, tool_type="ocs_attachments")
        assert set(chat_attachment.files.values_list("id", flat=True)) == {file_a.id, file_b.id}


class TestWhatsappAttachmentHydrationStage:
    def setup_method(self):
        self.stage = WhatsappAttachmentHydrationStage()

    def _image_message(self, ctu="image"):
        msg = MagicMock()
        msg.attachment_mime_type = ctu
        msg.attachments = []
        return msg

    def test_skips_when_session_missing(self):
        ctx = make_context(message=self._image_message(), experiment_session=None)
        assert self.stage.should_run(ctx) is False

    def test_skips_when_attachments_already_populated(self):
        msg = self._image_message()
        msg.attachments = [MagicMock()]
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    def test_skips_for_text_message(self):
        """Messages without attachment_mime_type (text) are skipped."""
        msg = MagicMock()
        msg.attachment_mime_type = None
        msg.attachments = []
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    def test_runs_for_meta_turn_image(self):
        """attachment_mime_type == 'image' (Meta Cloud API / TurnIO format)."""
        ctx = make_context(message=self._image_message("image"), experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is True

    def test_runs_for_twilio_image(self):
        """attachment_mime_type starts with 'image/' (Twilio MIME type format)."""
        ctx = make_context(message=self._image_message("image/png"), experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is True

    @pytest.mark.parametrize(
        "mime",
        [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/csv",
        ],
    )
    def test_runs_for_document_mime_types(self, mime):
        """Any non-empty, non-voice MIME triggers the stage so documents are hydrated."""
        ctx = make_context(message=self._image_message(mime), experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is True

    @pytest.mark.django_db()
    def test_process_populates_attachments(self):
        """process() downloads the image via the messaging service, persists it,
        and hydrates ctx.message.attachments with an Attachment referencing the new File."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)

        msg = MagicMock()
        msg.attachment_mime_type = "image"
        msg.attachments = []
        msg.message_text = ""

        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_media.return_value = (
            b"\x89PNG\r\n\x1a\n",
            "image/png",
        )

        self.stage.process(ctx)

        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "ocs_attachments"
        assert str(session.id) in msg.attachments[0].download_link

    @pytest.mark.django_db()
    def test_process_creates_chat_attachment_link(self):
        """The persisted File must be linked to the session's Chat via ChatAttachment
        so the experiments:download_file view's join succeeds when an LLM provider
        fetches the download_link."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)

        msg = MagicMock()
        msg.attachment_mime_type = "image"
        msg.attachments = []
        msg.message_text = ""

        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_media.return_value = (
            b"\x89PNG\r\n\x1a\n",
            "image/png",
        )

        self.stage.process(ctx)

        chat_attachment = ChatAttachment.objects.get(chat=session.chat, tool_type="ocs_attachments")
        file_id = msg.attachments[0].file_id
        assert chat_attachment.files.filter(id=file_id).exists()

    @pytest.mark.django_db()
    def test_hydrated_file_is_reachable_via_download_file_view(self, client):
        """End-to-end: after hydration, the experiments:download_file view should
        return the file (200), proving the File → ChatAttachment → Chat →
        ExperimentSession join resolves. Without the ChatAttachment linkage the
        view 404s."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)

        msg = MagicMock()
        msg.attachment_mime_type = "image"
        msg.attachments = []
        msg.message_text = ""

        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_media.return_value = (
            b"\x89PNG\r\n\x1a\n",
            "image/png",
        )

        self.stage.process(ctx)
        file_id = msg.attachments[0].file_id

        response = client.get(reverse("experiments:download_file", args=[experiment.team.slug, session.id, file_id]))
        assert response.status_code == 200

    @pytest.mark.django_db()
    def test_process_persists_document_with_provider_filename(self):
        """Documents must keep the provider-supplied filename rather than a generic 'image' fallback."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)

        msg = MagicMock()
        msg.attachment_mime_type = "application/pdf"
        msg.attachment_filename = "invoice.pdf"
        msg.attachments = []
        msg.message_text = ""

        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_media.return_value = (
            b"%PDF-1.4 fake",
            "application/pdf",
        )

        self.stage.process(ctx)

        from apps.files.models import File  # noqa: PLC0415

        assert len(msg.attachments) == 1
        file_id = msg.attachments[0].file_id
        file_obj = File.objects.get(id=file_id)
        assert file_obj.name == "invoice.pdf"
        assert file_obj.content_type == "application/pdf"

    @pytest.mark.django_db()
    def test_process_falls_back_to_family_when_filename_missing(self):
        """When the provider doesn't send a filename (image messages), a family-based name is used.
        File.create appends an extension inferred from the content type if the name has none."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)

        msg = MagicMock()
        msg.attachment_mime_type = "image"
        msg.attachment_filename = None
        msg.attachments = []
        msg.message_text = ""

        ctx = make_context(message=msg, experiment=experiment, experiment_session=session)
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_media.return_value = (
            b"\x89PNG\r\n\x1a\n",
            "image/png",
        )

        self.stage.process(ctx)

        from apps.files.models import File  # noqa: PLC0415

        file_obj = File.objects.get(id=msg.attachments[0].file_id)
        # File.create appends a content-type-derived extension when none is provided.
        assert file_obj.name.startswith("image")
        assert file_obj.content_type == "image/png"
