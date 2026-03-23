from unittest.mock import MagicMock, patch

from telebot.apihelper import ApiTelegramException

from apps.channels.channels_v2.stages.terminal import SendingErrorHandlerStage
from apps.channels.tests.channels.conftest import make_context
from apps.experiments.models import ParticipantData


class TestSendingErrorHandlerStage:
    def setup_method(self):
        self.stage = SendingErrorHandlerStage()

    def test_should_not_run_without_exception(self):
        ctx = make_context(sending_exception=None)
        assert self.stage.should_run(ctx) is False

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
            sending_exception=exc,
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
            sending_exception=exc,
            participant_identifier="unknown_user",
        )

        self.stage(ctx)

        assert any("Participant data not found" in e for e in ctx.processing_errors)

    def test_non_telegram_exception_noop(self):
        exc = RuntimeError("some other error")
        ctx = make_context(sending_exception=exc)

        # Should not raise
        self.stage(ctx)

        # No additional processing errors beyond what was already set
        assert len(ctx.processing_errors) == 0
