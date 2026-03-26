from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.stages.core import ParticipantValidationStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message


class TestParticipantValidationStage:
    def setup_method(self):
        self.stage = ParticipantValidationStage()

    def test_public_experiment_allows_participant(self):
        experiment = MagicMock()
        experiment.is_public = True
        ctx = make_context(experiment=experiment, participant_allowed=False)

        self.stage(ctx)

        assert ctx.participant_allowed is True

    def test_private_allowed_participant(self):
        experiment = MagicMock()
        experiment.is_public = False
        experiment.is_participant_allowed.return_value = True
        ctx = make_context(experiment=experiment, participant_allowed=False)

        self.stage(ctx)

        assert ctx.participant_allowed is True
        experiment.is_participant_allowed.assert_called_once_with(ctx.participant_identifier)

    def test_private_not_allowed_raises_early_exit(self):
        experiment = MagicMock()
        experiment.is_public = False
        experiment.is_participant_allowed.return_value = False
        ctx = make_context(experiment=experiment, participant_allowed=False)

        with pytest.raises(EarlyExitResponse):
            self.stage(ctx)

        assert ctx.participant_allowed is False

    def test_participant_identifier_set_from_message(self):
        experiment = MagicMock()
        experiment.is_public = True
        msg = text_message(participant_id="custom_id_456")
        ctx = make_context(experiment=experiment, message=msg, participant_identifier=None)

        self.stage(ctx)

        assert ctx.participant_identifier == "custom_id_456"
