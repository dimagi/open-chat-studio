from unittest.mock import MagicMock, patch

from apps.channels.channels_v2.stages.core import BotInteractionStage
from apps.channels.tests.channels.conftest import StubCallbacks, make_context


class TestBotInteractionStage:
    def setup_method(self):
        self.stage = BotInteractionStage()

    def test_should_not_run_without_query(self):
        ctx = make_context(user_query=None)
        assert self.stage.should_run(ctx) is False

    @patch("apps.channels.channels_v2.stages.core.get_bot")
    def test_calls_submit_input_callback(self, mock_get_bot):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = MagicMock(content="response", get_attached_files=lambda: [])
        mock_get_bot.return_value = mock_bot
        callbacks = StubCallbacks()
        ctx = make_context(user_query="Hello", callbacks=callbacks)

        self.stage(ctx)

        assert len(callbacks.submit_input_calls) == 1

    @patch("apps.channels.channels_v2.stages.core.get_bot")
    def test_creates_bot_lazily(self, mock_get_bot):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = MagicMock(content="response", get_attached_files=lambda: [])
        mock_get_bot.return_value = mock_bot
        ctx = make_context(user_query="Hello", bot=None)

        self.stage(ctx)

        mock_get_bot.assert_called_once()
        assert ctx.bot is mock_bot

    @patch("apps.channels.channels_v2.stages.core.get_bot")
    def test_reuses_existing_bot(self, mock_get_bot):
        existing_bot = MagicMock()
        existing_bot.process_input.return_value = MagicMock(content="response", get_attached_files=lambda: [])
        ctx = make_context(user_query="Hello", bot=existing_bot)

        self.stage(ctx)

        mock_get_bot.assert_not_called()
        existing_bot.process_input.assert_called_once()

    @patch("apps.channels.channels_v2.stages.core.get_bot")
    def test_sets_bot_response_and_files(self, mock_get_bot):
        files = [MagicMock(), MagicMock()]
        mock_bot = MagicMock()
        bot_response = MagicMock(content="bot says hi")
        bot_response.get_attached_files.return_value = files
        mock_bot.process_input.return_value = bot_response
        mock_get_bot.return_value = mock_bot
        ctx = make_context(user_query="Hello")

        self.stage(ctx)

        assert ctx.bot_response is bot_response
        assert ctx.files_to_send == files
