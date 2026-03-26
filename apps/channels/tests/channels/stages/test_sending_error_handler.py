from unittest.mock import MagicMock, patch

import pytest
from telebot.apihelper import ApiTelegramException

from apps.channels.channels_v2.stages.terminal import (
    FileDeliveryFailure,
    MessageDeliveryFailure,
    SendingErrorHandlerStage,
)
from apps.channels.tests.channels.conftest import make_context
from apps.experiments.models import ParticipantData


class TestSendingErrorHandlerStage:
    def setup_method(self):
        self.stage = SendingErrorHandlerStage()

    def test_should_not_run_without_exceptions(self):
        ctx = make_context()
        assert self.stage.should_run(ctx) is False

    def test_should_run_with_sending_exceptions(self):
        ctx = make_context(sending_exceptions=[MagicMock()])
        assert self.stage.should_run(ctx) is True

    @patch("apps.experiments.models.ParticipantData.objects")
    def test_telegram_403_revokes_consent(self, mock_pd_objects):
        participant_data = MagicMock()
        mock_pd_objects.get.return_value = participant_data
        exc = ApiTelegramException(
            "sendMessage",
            MagicMock(status_code=403),
            {"error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        )
        ctx = make_context(
            sending_exceptions=[exc],
            participant_identifier="blocked_user",
        )

        self.stage(ctx)

        participant_data.update_consent.assert_called_once_with(False)

    @patch("apps.experiments.models.ParticipantData.objects")
    def test_telegram_403_participant_not_found(self, mock_pd_objects):
        mock_pd_objects.get.side_effect = ParticipantData.DoesNotExist
        exc = ApiTelegramException(
            "sendMessage",
            MagicMock(status_code=403),
            {"error_code": 403, "description": "Forbidden: bot was blocked by the user"},
        )
        ctx = make_context(
            sending_exceptions=[exc],
            participant_identifier="unknown_user",
        )

        self.stage(ctx)

        assert any("Participant data not found" in e for e in ctx.processing_errors)

    @patch("apps.channels.channels_v2.stages.terminal.message_delivery_failure_notification")
    def test_message_delivery_failure_sends_notification_and_does_not_reraise(self, mock_notify):
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
