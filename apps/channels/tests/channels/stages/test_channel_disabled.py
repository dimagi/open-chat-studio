from unittest.mock import MagicMock

import pytest

from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.stages.core import ChannelDisabledStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message


def make_channel(*, enabled=True, disabled_message=""):
    channel = MagicMock()
    channel.enabled = enabled
    channel.is_disabled = not enabled
    channel.disabled_message = disabled_message
    return channel


class TestChannelDisabledStageShouldRun:
    def setup_method(self):
        self.stage = ChannelDisabledStage()

    def test_skips_when_channel_enabled(self):
        ctx = make_context(experiment_channel=make_channel(enabled=True))
        assert self.stage.should_run(ctx) is False

    def test_runs_when_channel_disabled(self):
        ctx = make_context(experiment_channel=make_channel(enabled=False))
        assert self.stage.should_run(ctx) is True


class TestChannelDisabledStageProcess:
    def setup_method(self):
        self.stage = ChannelDisabledStage()

    def test_enabled_channel_is_a_no_op(self):
        ctx = make_context(experiment_channel=make_channel(enabled=True))

        self.stage(ctx)  # does not raise

        assert ctx.early_exit_response is None

    def test_disabled_with_message_exits_with_that_message(self):
        channel = make_channel(enabled=False, disabled_message="We are down for maintenance")
        ctx = make_context(experiment_channel=channel)

        with pytest.raises(EarlyExitResponse) as exc_info:
            self.stage(ctx)

        assert exc_info.value.response == "We are down for maintenance"

    def test_disabled_without_message_aborts_silently(self):
        ctx = make_context(experiment_channel=make_channel(enabled=False))

        with pytest.raises(EarlyAbort):
            self.stage(ctx)

    def test_sets_recipient_for_the_sending_stage(self):
        """ParticipantValidationStage never runs, so this stage must set the recipient itself."""
        channel = make_channel(enabled=False, disabled_message="Closed")
        ctx = make_context(
            message=text_message(participant_id="+27820001111"),
            experiment_channel=channel,
            participant_identifier=None,
        )

        with pytest.raises(EarlyExitResponse):
            self.stage(ctx)

        assert ctx.participant_identifier == "+27820001111"
