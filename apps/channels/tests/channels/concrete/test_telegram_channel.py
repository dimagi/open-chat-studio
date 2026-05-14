from unittest.mock import MagicMock, patch

import pytest
from telebot.apihelper import ApiTelegramException

from apps.channels.channels_v2.stages.terminal import MessageDeliveryFailure
from apps.channels.channels_v2.telegram_channel import TelegramChannel, handle_telegram_block
from apps.channels.tests.channels.conftest import make_context
from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


@pytest.fixture()
def _patched_telebot():
    with patch("apps.channels.channels_v2.telegram_channel.TeleBot") as mock_telebot:
        yield mock_telebot


def _make_channel(_patched_telebot, *, experiment_session=None):
    experiment = MagicMock()
    experiment_channel = MagicMock()
    experiment_channel.extra_data = {"bot_token": "fake_token"}
    return TelegramChannel(
        experiment=experiment,
        experiment_channel=experiment_channel,
        experiment_session=experiment_session,
    )


class TestTelegramChannelInit:
    def test_bot_is_constructed_from_extra_data(self, _patched_telebot):
        """The bot token from ``ExperimentChannel.extra_data`` is forwarded to ``TeleBot``."""
        channel = _make_channel(_patched_telebot)
        _patched_telebot.assert_called_once_with("fake_token", threaded=False)
        assert channel.telegram_bot is _patched_telebot.return_value

    def test_accepts_optional_session(self, _patched_telebot):
        """``experiment_session`` is optional at construction time (e.g. when handling the first
        inbound message before a session exists)."""
        session = MagicMock()
        channel = _make_channel(_patched_telebot, experiment_session=session)
        assert channel.experiment_session is session


class TestTelegramChannelCanSendFile:
    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected"),
        [
            ("image/jpeg", 5 * 1024 * 1024, True),
            ("image/jpeg", 15 * 1024 * 1024, False),  # over 10MB image limit
            ("video/mp4", 40 * 1024 * 1024, True),
            ("video/mp4", 60 * 1024 * 1024, False),  # over 50MB media limit
            ("application/pdf", 30 * 1024 * 1024, True),
            ("text/plain", 1 * 1024 * 1024, False),  # unsupported top-level type for telegram
        ],
    )
    def test_size_and_type_limits(self, _patched_telebot, content_type, content_size, expected):
        """Per-MIME-type size limits from ``can_send_on_telegram`` flow through ``_can_send_file``."""
        channel = _make_channel(_patched_telebot)
        file = MagicMock()
        file.content_type = content_type
        file.content_size = content_size
        assert channel._can_send_file(file) is expected

    def test_missing_content_type_returns_false(self, _patched_telebot):
        """A file with no content type is rejected outright rather than guessed."""
        channel = _make_channel(_patched_telebot)
        file = MagicMock()
        file.content_type = None
        file.content_size = None
        assert channel._can_send_file(file) is False


def _telegram_blocked_failure() -> MessageDeliveryFailure:
    api_exc = ApiTelegramException(
        "sendMessage",
        MagicMock(status_code=403),
        {"error_code": 403, "description": "Forbidden: bot was blocked by the user"},
    )
    return MessageDeliveryFailure(api_exc, context="text message")


class TestHandleTelegramBlock:
    def test_ignores_non_message_delivery_failures(self):
        """Raw exceptions that aren't wrapped in ``MessageDeliveryFailure`` are skipped so the
        chain falls through to the next handler or the generic notification path."""
        ctx = make_context()
        assert handle_telegram_block(ctx, RuntimeError("other")) is False

    def test_ignores_non_telegram_api_exceptions(self):
        """A ``MessageDeliveryFailure`` wrapping a non-Telegram exception is not this handler's
        concern; returning False lets the chain continue."""
        ctx = make_context()
        exc = MessageDeliveryFailure(RuntimeError("other"), context="text message")
        assert handle_telegram_block(ctx, exc) is False

    def test_ignores_non_403_telegram_errors(self):
        """Only the 403 "bot was blocked" case should revoke consent; other Telegram API errors
        fall through to the generic notification path."""
        ctx = make_context()
        api_exc = ApiTelegramException(
            "sendMessage",
            MagicMock(status_code=500),
            {"error_code": 500, "description": "Internal Server Error"},
        )
        exc = MessageDeliveryFailure(api_exc, context="text message")
        assert handle_telegram_block(ctx, exc) is False

    @pytest.mark.django_db()
    def test_revokes_consent_when_ctx_holds_published_version(self):
        """Production calls pass ``experiment.default_version`` to the channel, so ``ctx.experiment``
        is a published version. ParticipantData rows are keyed to the working version, so the
        handler must walk back via ``ParticipantData.objects.for_experiment()`` to find the row
        and flip consent off."""
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

        ctx = make_context(experiment=published_version, participant_identifier="blocked_user")

        assert handle_telegram_block(ctx, _telegram_blocked_failure()) is True
        participant_data.refresh_from_db()
        assert participant_data.system_metadata["consent"] is False
        assert ctx.processing_errors == []

    @pytest.mark.django_db()
    def test_participant_not_found_records_processing_error(self):
        """When a 403 fires for an unknown participant, the handler still claims the exception
        (returns True) but records a soft processing error rather than raising."""
        experiment = ExperimentFactory.create()
        ctx = make_context(experiment=experiment, participant_identifier="unknown_user")

        assert handle_telegram_block(ctx, _telegram_blocked_failure()) is True
        assert any("Participant data not found" in e for e in ctx.processing_errors)
