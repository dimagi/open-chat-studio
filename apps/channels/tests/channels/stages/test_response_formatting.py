from unittest.mock import MagicMock, patch

from apps.channels.channels_v2.stages.core import ResponseFormattingStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message
from apps.chat.exceptions import AudioSynthesizeException
from apps.experiments.models import VoiceResponseBehaviours


class TestResponseFormattingStage:
    def setup_method(self):
        self.stage = ResponseFormattingStage()

    def test_should_not_run_without_bot_response(self):
        ctx = make_context(bot_response=None)
        assert self.stage.should_run(ctx) is False

    def test_text_path_sets_formatted_message(self):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = None
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
        )

        self.stage(ctx)

        assert ctx.formatted_message is not None
        assert "Hello user" in ctx.formatted_message
        assert ctx.voice_audio is None

    def test_voice_always_with_voice_provider(self):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = MagicMock()
        experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
        experiment.voice_provider.get_speech_service.return_value.synthesize_voice.return_value = MagicMock()
        capabilities = make_capabilities(supports_voice=True)
        msg = text_message()
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
            message=msg,
        )

        self.stage(ctx)

        assert ctx.voice_audio is not None

    def test_voice_never(self):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = MagicMock()
        experiment.voice_response_behaviour = VoiceResponseBehaviours.NEVER
        capabilities = make_capabilities(supports_voice=True)
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
        )

        self.stage(ctx)

        assert ctx.voice_audio is None
        assert ctx.formatted_message is not None

    def test_voice_reciprocal_voice_input(self):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = MagicMock()
        experiment.voice_response_behaviour = VoiceResponseBehaviours.RECIPROCAL
        experiment.voice_provider.get_speech_service.return_value.synthesize_voice.return_value = MagicMock()
        capabilities = make_capabilities(supports_voice=True)
        msg = audio_message()
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
            message=msg,
        )

        self.stage(ctx)

        assert ctx.voice_audio is not None

    def test_voice_reciprocal_text_input(self):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = MagicMock()
        experiment.voice_response_behaviour = VoiceResponseBehaviours.RECIPROCAL
        capabilities = make_capabilities(supports_voice=True)
        msg = text_message()
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
            message=msg,
        )

        self.stage(ctx)

        assert ctx.voice_audio is None
        assert ctx.formatted_message is not None

    @patch("apps.channels.channels_v2.stages.core.audio_synthesis_failure_notification")
    def test_voice_synthesis_failure_fallback(self, mock_notification):
        bot_response = MagicMock()
        bot_response.content = "Hello user"
        bot_response.get_attached_files.return_value = []
        experiment = MagicMock()
        experiment.synthetic_voice = MagicMock()
        experiment.voice_response_behaviour = VoiceResponseBehaviours.ALWAYS
        experiment.voice_provider.get_speech_service.return_value.synthesize_voice.side_effect = (
            AudioSynthesizeException("synthesis failed")
        )
        capabilities = make_capabilities(supports_voice=True)
        msg = text_message()
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
            message=msg,
        )

        self.stage(ctx)

        assert ctx.voice_audio is None
        assert ctx.formatted_message is not None
        mock_notification.assert_called_once()

    def test_unsupported_files_appended_as_links(self):
        file1 = MagicMock()
        file1.name = "doc.pdf"
        file1.content_type = "application/pdf"
        file1.citation_text = "doc.pdf"
        file1.download_link.return_value = "https://example.com/doc.pdf"
        bot_response = MagicMock()
        bot_response.content = "Check this file"
        bot_response.get_attached_files.return_value = [file1]
        experiment = MagicMock()
        experiment.synthetic_voice = None
        session = MagicMock()
        session.id = 1
        capabilities = make_capabilities(supports_files=False)
        ctx = make_context(
            bot_response=bot_response,
            experiment=experiment,
            capabilities=capabilities,
            experiment_session=session,
            files_to_send=[file1],
        )

        self.stage(ctx)

        assert "doc.pdf" in ctx.formatted_message
        assert "https://example.com/doc.pdf" in ctx.formatted_message
