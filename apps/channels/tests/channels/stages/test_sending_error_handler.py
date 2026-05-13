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
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


def _telegram_blocked_failure() -> MessageDeliveryFailure:
    api_exc = ApiTelegramException(
        "sendMessage",
        MagicMock(status_code=403),
        {"error_code": 403, "description": "Forbidden: bot was blocked by the user"},
    )
    return MessageDeliveryFailure(api_exc, context="text message")


class TestSendingErrorHandlerStage:
    def setup_method(self):
        self.stage = SendingErrorHandlerStage()

    def test_should_not_run_without_exceptions(self):
        ctx = make_context()
        assert self.stage.should_run(ctx) is False

    def test_should_run_with_sending_exceptions(self):
        ctx = make_context(sending_exceptions=[MagicMock()])
        assert self.stage.should_run(ctx) is True

    @pytest.mark.django_db()
    def test_telegram_403_revokes_consent_when_ctx_holds_published_version(self):
        """Production calls pass experiment.default_version to the channel, so ctx.experiment
        is a published version. ParticipantData rows are keyed to the working version, so the
        handler must walk back via ParticipantData.objects.for_experiment()."""
        working_experiment = ExperimentFactory.create()
        published_version = working_experiment.create_new_version(make_default=True)
        assert published_version.is_a_version
        assert published_version.working_version_id == working_experiment.id

        participant = ParticipantFactory.create(team=working_experiment.team, identifier="blocked_user")
        participant_data = ParticipantData.objects.create(
            team=working_experiment.team,
            experiment=working_experiment,
            participant=participant,
            system_metadata={"consent": True},
        )

        ctx = make_context(
            experiment=published_version,
            sending_exceptions=[_telegram_blocked_failure()],
            participant_identifier="blocked_user",
        )

        self.stage(ctx)

        participant_data.refresh_from_db()
        assert participant_data.system_metadata["consent"] is False
        assert ctx.processing_errors == []

    @pytest.mark.django_db()
    def test_telegram_403_participant_not_found(self):
        experiment = ExperimentFactory.create()
        ctx = make_context(
            experiment=experiment,
            sending_exceptions=[_telegram_blocked_failure()],
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
