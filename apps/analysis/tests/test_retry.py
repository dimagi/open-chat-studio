import contextlib
from unittest.mock import MagicMock, patch


class TestTranscriptAnalysisRetry:
    @patch("apps.analysis.tasks.with_llm_retry")
    @patch("apps.analysis.tasks.get_model_parameters")
    def test_llm_wrapped_with_retry(self, mock_get_params, mock_with_retry):
        """Verify that the LLM is wrapped with retry logic."""
        from apps.analysis.tasks import process_transcript_analysis

        mock_get_params.return_value = {"temperature": 0.1}
        mock_llm = MagicMock()
        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm

        mock_analysis = MagicMock()
        mock_analysis.llm_provider.get_llm_service.return_value = mock_llm_service
        mock_analysis.llm_provider_model.name = "gpt-4"
        mock_analysis.translation_language = None
        mock_analysis.sessions.all.return_value.prefetch_related.return_value = []
        mock_analysis.queries.all.return_value.order_by.return_value = []

        with patch("apps.analysis.tasks.TranscriptAnalysis.objects.select_related") as mock_select:
            mock_select.return_value.get.return_value = mock_analysis
            with patch("apps.analysis.tasks.current_team"):
                with patch("apps.analysis.tasks.ProgressRecorder"):
                    with contextlib.suppress(Exception):
                        process_transcript_analysis(1)

        mock_with_retry.assert_called_once_with(mock_llm)


class TestTranslationRetry:
    @patch("apps.analysis.translation.with_llm_retry")
    @patch("apps.analysis.translation.get_model_parameters")
    def test_llm_wrapped_with_retry(self, mock_get_params, mock_with_retry):
        """Verify that the translation LLM is wrapped with retry logic."""
        from apps.analysis.translation import translate_messages_with_llm

        mock_get_params.return_value = {"temperature": 0.1}
        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_llm_with_retry.invoke.return_value.text = "[]"
        mock_with_retry.return_value = mock_llm_with_retry

        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm

        mock_provider = MagicMock()
        mock_provider.get_llm_service.return_value = mock_llm_service

        mock_model = MagicMock()
        mock_model.name = "gpt-4"

        mock_message = MagicMock()
        mock_message.translations = {}
        mock_message.id = 1
        mock_message.content = "Hello"
        mock_message.role = "user"

        with patch("apps.analysis.translation.current_team"):
            translate_messages_with_llm([mock_message], "es", mock_provider, mock_model)

        mock_with_retry.assert_called_once_with(mock_llm)
