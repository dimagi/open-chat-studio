from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.stages.terminal import (
    FileDeliveryFailure,
    MessageDeliveryFailure,
    SendingErrorHandlerStage,
)
from apps.channels.tests.channels.conftest import make_context


class TestSendingErrorHandlerStage:
    def setup_method(self):
        self.stage = SendingErrorHandlerStage()

    def test_should_not_run_without_exceptions(self):
        """The stage is a no-op when no sending exceptions accumulated upstream."""
        ctx = make_context()
        assert self.stage.should_run(ctx) is False

    def test_should_run_with_sending_exceptions(self):
        """The stage activates as soon as any sending exception is recorded."""
        ctx = make_context(sending_exceptions=[MagicMock()])
        assert self.stage.should_run(ctx) is True

    def test_handler_chain_claims_exception_and_skips_default(self):
        """A handler returning True stops the chain and suppresses the generic notification.

        Also asserts the chain keeps walking past handlers that return False, so the
        first non-claiming handler does not short-circuit subsequent ones.
        """
        claiming_handler = MagicMock(return_value=True)
        passing_handler = MagicMock(return_value=False)
        stage = SendingErrorHandlerStage(error_handlers=[passing_handler, claiming_handler])

        exc = MessageDeliveryFailure(RuntimeError("boom"), context="text message")
        ctx = make_context(sending_exceptions=[exc])

        with patch("apps.channels.channels_v2.stages.terminal.message_delivery_failure_notification") as mock_notify:
            stage(ctx)

        passing_handler.assert_called_once_with(ctx, exc)
        claiming_handler.assert_called_once_with(ctx, exc)
        mock_notify.assert_not_called()

    def test_handler_chain_falls_through_when_no_handler_claims(self):
        """If every handler returns False, the stage falls back to its built-in notification path."""
        passing_handler = MagicMock(return_value=False)
        stage = SendingErrorHandlerStage(error_handlers=[passing_handler])

        exc = MessageDeliveryFailure(RuntimeError("boom"), context="text message")
        experiment_channel = MagicMock()
        experiment_channel.platform_enum.title.return_value = "Telegram"
        ctx = make_context(sending_exceptions=[exc], experiment_channel=experiment_channel)

        with patch("apps.channels.channels_v2.stages.terminal.message_delivery_failure_notification") as mock_notify:
            stage(ctx)

        passing_handler.assert_called_once_with(ctx, exc)
        mock_notify.assert_called_once()

    @patch("apps.channels.channels_v2.stages.terminal.message_delivery_failure_notification")
    def test_message_delivery_failure_sends_notification_and_does_not_reraise(self, mock_notify):
        """A wrapped ``MessageDeliveryFailure`` triggers a team notification and is swallowed,
        so a single send failure does not poison the rest of the pipeline."""
        exc = MessageDeliveryFailure(
            RuntimeError("network error"),
            context="text message",
        )
        experiment = MagicMock()
        session = MagicMock()
        experiment_channel = MagicMock()
        experiment_channel.platform_enum.title.return_value = "Telegram"
        ctx = make_context(
            sending_exceptions=[exc],
            experiment=experiment,
            experiment_session=session,
            experiment_channel=experiment_channel,
        )

        self.stage(ctx)

        mock_notify.assert_called_once_with(
            experiment,
            session=session,
            platform_title="Telegram",
            context="text message",
        )

    def test_unknown_exception_reraises(self):
        """Unknown exceptions in ctx.sending_exceptions propagate to fail the task."""
        exc = RuntimeError("unexpected platform error")
        ctx = make_context(sending_exceptions=[exc])

        with pytest.raises(RuntimeError, match="unexpected platform error"):
            self.stage(ctx)

    @patch("apps.channels.channels_v2.stages.terminal.file_delivery_failure_notification")
    def test_file_exception_sends_notification(self, mock_notify):
        """A ``FileDeliveryFailure`` triggers a file-specific notification and is also swallowed."""
        file_obj = MagicMock()
        file_obj.content_type = "image/png"
        experiment = MagicMock()
        session = MagicMock()
        session.id = 42
        experiment_channel = MagicMock()
        experiment_channel.platform_enum.title.return_value = "Telegram"
        exc = FileDeliveryFailure(
            RuntimeError("upload failed"),
            file=file_obj,
        )
        ctx = make_context(
            sending_exceptions=[exc],
            experiment=experiment,
            experiment_session=session,
            experiment_channel=experiment_channel,
        )

        self.stage(ctx)

        mock_notify.assert_called_once_with(
            experiment,
            platform_title="Telegram",
            content_type="image/png",
            session=session,
        )
