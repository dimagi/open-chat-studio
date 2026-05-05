from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.stages.core import AttachmentHydrationStage
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
