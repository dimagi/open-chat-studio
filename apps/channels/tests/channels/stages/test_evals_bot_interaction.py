from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.stages.core import EvalsBotInteractionStage
from apps.channels.tests.channels.conftest import make_context


class TestEvalsBotInteractionStage:
    """Unit tests for EvalsBotInteractionStage.

    This stage replaces BotInteractionStage for evaluations.
    It uses EvalsBot instead of get_bot(), reading participant_data
    from ctx.channel_context.
    """

    def test_should_run_when_user_query_present(self):
        ctx = make_context(user_query="hello")
        stage = EvalsBotInteractionStage()
        assert stage.should_run(ctx) is True

    def test_should_not_run_when_user_query_is_none(self):
        ctx = make_context(user_query=None)
        stage = EvalsBotInteractionStage()
        assert stage.should_run(ctx) is False

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_creates_evals_bot_with_participant_data(self, mock_evals_bot_cls):
        participant_data = {"userid": "1234", "name": "Test User"}
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = MagicMock(get_attached_files=MagicMock(return_value=[]))
        mock_evals_bot_cls.return_value = mock_bot

        session = MagicMock()
        experiment = MagicMock()

        ctx = make_context(
            user_query="test message",
            experiment_session=session,
            experiment=experiment,
            channel_context={"participant_data": participant_data},
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        mock_evals_bot_cls.assert_called_once_with(
            session,
            experiment,
            ctx.trace_service,
            participant_data=participant_data,
        )

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_sets_bot_response_and_files(self, mock_evals_bot_cls):
        mock_response = MagicMock()
        mock_files = [MagicMock(), MagicMock()]
        mock_response.get_attached_files.return_value = mock_files

        mock_bot = MagicMock()
        mock_bot.process_input.return_value = mock_response
        mock_evals_bot_cls.return_value = mock_bot

        ctx = make_context(
            user_query="test query",
            channel_context={"participant_data": {}},
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        assert ctx.bot_response == mock_response
        assert ctx.files_to_send == mock_files
        assert ctx.bot == mock_bot

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_passes_attachments_to_bot(self, mock_evals_bot_cls):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = MagicMock(get_attached_files=MagicMock(return_value=[]))
        mock_evals_bot_cls.return_value = mock_bot

        message = MagicMock()
        message.attachments = [MagicMock()]

        ctx = make_context(
            message=message,
            user_query="test",
            channel_context={"participant_data": {}},
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        mock_bot.process_input.assert_called_once_with(
            "test",
            attachments=message.attachments,
            human_message=ctx.human_message,
        )

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_handles_no_attached_files(self, mock_evals_bot_cls):
        mock_response = MagicMock()
        mock_response.get_attached_files.return_value = None

        mock_bot = MagicMock()
        mock_bot.process_input.return_value = mock_response
        mock_evals_bot_cls.return_value = mock_bot

        ctx = make_context(
            user_query="test",
            channel_context={"participant_data": {}},
        )

        stage = EvalsBotInteractionStage()
        stage.process(ctx)

        assert ctx.files_to_send == []

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_exception_propagates(self, mock_evals_bot_cls):
        """Exceptions are NOT caught — they propagate to the pipeline catch-all."""
        mock_bot = MagicMock()
        mock_bot.process_input.side_effect = RuntimeError("bot error")
        mock_evals_bot_cls.return_value = mock_bot

        ctx = make_context(
            user_query="test",
            channel_context={"participant_data": {}},
        )

        stage = EvalsBotInteractionStage()

        with pytest.raises(RuntimeError, match="bot error"):
            stage.process(ctx)
