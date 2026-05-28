from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.stages.core import AttachmentHydrationStage, WhatsappAttachmentHydrationStage
from apps.channels.datamodels import Attachment, BaseMessage
from apps.channels.tests.channels.conftest import make_context
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
        ctx.experiment_channel.messaging_provider.get_messaging_service.return_value.get_inbound_image.return_value = (
            b"\x89PNG\r\n\x1a\n",
            "image/png",
        )

        self.stage.process(ctx)

        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "ocs_attachments"
        assert str(session.id) in msg.attachments[0].download_link
