from unittest.mock import MagicMock, patch

from apps.channels.channels_v2.stages.core import EvalsBotInteractionStage
from apps.channels.tests.channels.conftest import make_context


class TestEvalsBotInteractionStage:
    def setup_method(self):
        self.stage = EvalsBotInteractionStage()

    def test_should_not_run_without_query(self):
        ctx = make_context(user_query=None)
        assert self.stage.should_run(ctx) is False

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_constructs_evals_bot_with_participant_data(self, mock_evals_bot_cls):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = MagicMock(content="response", get_attached_files=lambda: [])
        mock_evals_bot_cls.return_value = mock_bot

        participant_data = {"userid": "1234"}
        experiment = MagicMock()
        session = MagicMock()
        trace_service = MagicMock()
        ctx = make_context(
            user_query="Hello",
            experiment=experiment,
            experiment_session=session,
            channel_context={"participant_data": participant_data},
            trace_service=trace_service,
        )

        self.stage(ctx)

        mock_evals_bot_cls.assert_called_once_with(
            session,
            experiment,
            trace_service,
            participant_data=participant_data,
        )
        assert ctx.bot is mock_bot

    @patch("apps.channels.channels_v2.stages.core.EvalsBot")
    def test_sets_bot_response_and_files(self, mock_evals_bot_cls):
        files = [MagicMock(), MagicMock()]
        bot_response = MagicMock(content="bot says hi")
        bot_response.get_attached_files.return_value = files
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = bot_response
        mock_evals_bot_cls.return_value = mock_bot

        ctx = make_context(user_query="Hello", channel_context={"participant_data": {}})

        self.stage(ctx)

        assert ctx.bot_response is bot_response
        assert ctx.files_to_send == files
