import pytest

from apps.channels.stages.core import ParticipantIdentifierStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
class TestParticipantIdentifierStage:
    def test_identifier_is_copied_from_the_message(self):
        ctx = make_context(
            experiment=ExperimentFactory.create(),
            message=text_message(participant_id="custom_id_456"),
            participant_identifier=None,
        )

        ParticipantIdentifierStage()(ctx)

        assert ctx.participant_identifier == "custom_id_456"

    def test_identifier_outside_a_stored_allowlist_is_not_refused(self):
        experiment = ExperimentFactory.create(participant_allowlist=["+27000000000"])
        ctx = make_context(experiment=experiment, message=text_message(participant_id="+27111111111"))

        ParticipantIdentifierStage()(ctx)

        assert ctx.participant_identifier == "+27111111111"
        assert ctx.early_exit_response is None
