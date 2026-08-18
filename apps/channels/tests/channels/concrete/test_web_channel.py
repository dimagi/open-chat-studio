from unittest.mock import MagicMock

import pytest

from apps.channels.api_channel import NoOpSender
from apps.channels.callbacks import ChannelCallbacks
from apps.channels.capabilities import ChannelCapabilities
from apps.channels.const import MESSAGE_TYPES
from apps.channels.stages.core import (
    ConsentFlowStage,
    SessionResolutionStage,
)
from apps.channels.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.channels.web_channel import WebChannel
from apps.chat.exceptions import ChannelException


class TestWebChannelInit:
    def test_requires_existing_session(self):
        with pytest.raises(ChannelException, match="WebChannel requires an existing session"):
            WebChannel(
                experiment=MagicMock(),
                experiment_channel=MagicMock(),
            )

    def test_accepts_session(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        assert channel.experiment_session is not None


class TestWebChannelPipeline:
    def _make_channel(self):
        return WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )

    @pytest.mark.parametrize(
        "stage_class",
        [SessionResolutionStage, ConsentFlowStage, ResponseSendingStage, SendingErrorHandlerStage],
        ids=["session_resolution", "consent_flow", "response_sending", "sending_error_handler"],
    )
    def test_pipeline_omits_stage(self, stage_class):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.core_stages + pipeline.terminal_stages]
        assert stage_class not in stage_types

    def test_pipeline_includes_persistence_and_activity_tracking(self):
        channel = self._make_channel()
        pipeline = channel._build_pipeline()

        stage_types = [type(s) for s in pipeline.terminal_stages]
        assert PersistenceStage in stage_types
        assert ActivityTrackingStage in stage_types


class TestWebChannelSender:
    def test_get_sender_returns_no_op_sender(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        sender = channel._get_sender()
        assert isinstance(sender, NoOpSender)


class TestWebChannelCallbacks:
    def test_get_callbacks_returns_base_callbacks(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        callbacks = channel._get_callbacks()
        assert isinstance(callbacks, ChannelCallbacks)


class TestWebChannelCapabilities:
    def test_capabilities_text_only_no_consent(self):
        channel = WebChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=MagicMock(),
        )
        caps = channel._get_capabilities()
        assert isinstance(caps, ChannelCapabilities)
        assert caps.supports_voice_replies is False
        assert caps.supports_files is False
        assert caps.supports_conversational_consent is False
        assert caps.supported_message_types == [MESSAGE_TYPES.TEXT]
