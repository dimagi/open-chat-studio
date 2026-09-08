from unittest.mock import MagicMock

import pytest

from apps.channels.channel_base import ChannelBase
from apps.channels.datamodels import BaseMessage
from apps.channels.exceptions import EarlyAbort
from apps.channels.stages.core import (
    DuplicateDeliveryStage,
    ParticipantResolverStage,
    SessionResolutionStage,
)
from apps.channels.tests.channels.conftest import make_context
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
class TestDuplicateDeliveryStage:
    def _context(self, experiment, external_ids):
        return make_context(
            experiment=experiment,
            message=BaseMessage(participant_id="123", message_text="Hello", external_ids=external_ids),
        )

    @pytest.mark.parametrize(
        ("candidate_ids", "aborts"),
        [
            pytest.param(["whatsapp:wamid.abc"], True, id="every-id-already-recorded"),
            pytest.param(["whatsapp:wamid.new"], False, id="fresh-id"),
        ],
    )
    def test_aborts_only_for_a_recorded_delivery(self, record_delivery, candidate_ids, aborts):
        experiment = ExperimentFactory()
        record_delivery(experiment.team, ["whatsapp:wamid.abc"])
        ctx = self._context(experiment, candidate_ids)

        if aborts:
            with pytest.raises(EarlyAbort):
                DuplicateDeliveryStage()(ctx)
        else:
            DuplicateDeliveryStage()(ctx)
            assert ctx.early_exit_response is None

    def test_does_not_run_without_ids(self):
        experiment = ExperimentFactory()
        ctx = self._context(experiment, [])

        assert DuplicateDeliveryStage().should_run(ctx) is False


class TestStagePlacement:
    """The stage has to run first, ahead of participant and session work.

    That ordering is the whole reason a replay creates no participant and no session on its way
    out; without it dedup still fires, but only after those side effects have happened.
    """

    def test_runs_before_participant_and_session_resolution(self):
        pipeline = ChannelBase._build_pipeline(MagicMock(attachment_hydration_stage_class=MagicMock))
        types = [type(stage) for stage in pipeline.core_stages]

        assert types.index(DuplicateDeliveryStage) == 0
        assert types.index(DuplicateDeliveryStage) < types.index(ParticipantResolverStage)
        assert types.index(DuplicateDeliveryStage) < types.index(SessionResolutionStage)
