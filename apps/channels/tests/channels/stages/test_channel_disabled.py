from unittest.mock import MagicMock

import pytest

from apps.channels.channel_base import ChannelBase
from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.stages.core import (
    ChannelDisabledStage,
    ConsentCheckStage,
    ParticipantResolverStage,
    SessionResolutionStage,
)
from apps.channels.tests.channels.conftest import make_context


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


class TestStagePlacement:
    """The stage has to sit behind the participant stages and ahead of session/bot work.

    Running it first would leave ``participant``/``participant_data`` unset -- CommCare
    Connect cannot send at all without the latter -- and would skip the platform consent
    gate, pushing the static reply at someone who blocked the bot.
    """

    def _core_stage_types(self):
        pipeline = ChannelBase._build_pipeline(MagicMock(attachment_hydration_stage_class=MagicMock))
        return [type(stage) for stage in pipeline.core_stages]

    def test_runs_after_participant_resolution_and_consent(self):
        types = self._core_stage_types()
        disabled_at = types.index(ChannelDisabledStage)

        assert types.index(ParticipantResolverStage) < disabled_at
        assert types.index(ConsentCheckStage) < disabled_at

    def test_runs_before_session_resolution(self):
        types = self._core_stage_types()

        assert types.index(ChannelDisabledStage) < types.index(SessionResolutionStage)
