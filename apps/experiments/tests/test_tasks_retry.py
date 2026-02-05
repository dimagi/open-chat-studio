from unittest.mock import MagicMock, patch


class TestPromptBuilderRetry:
    @patch("apps.experiments.tasks.with_llm_retry")
    @patch("apps.experiments.tasks.create_conversation")
    @patch("apps.experiments.tasks.LlmProvider.objects.get")
    @patch("apps.experiments.tasks.LlmProviderModel.objects.get")
    @patch("apps.experiments.tasks.CustomUser.objects.get")
    @patch("apps.experiments.tasks.SourceMaterial.objects.filter")
    @patch("apps.experiments.tasks.PromptBuilderHistory.objects.create")
    def test_llm_wrapped_with_retry(
        self,
        mock_history_create,
        mock_source_filter,
        mock_user_get,
        mock_model_get,
        mock_provider_get,
        mock_create_conv,
        mock_with_retry,
    ):
        """Verify that the prompt builder LLM is wrapped with retry logic."""
        from apps.experiments.tasks import get_prompt_builder_response_task

        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_with_retry.return_value = mock_llm_with_retry

        mock_llm_service = MagicMock()
        mock_llm_service.get_chat_model.return_value = mock_llm
        mock_provider_get.return_value.get_llm_service.return_value = mock_llm_service
        mock_model_get.return_value.name = "gpt-4"
        mock_user_get.return_value = MagicMock()
        mock_source_filter.return_value.first.return_value = None

        mock_conversation = MagicMock()
        mock_conversation.predict.return_value = ("response", 10, 5)
        mock_create_conv.return_value = mock_conversation

        data_dict = {
            "provider": 1,
            "providerModelId": 1,
            "messages": [],
            "prompt": "test prompt",
            "sourceMaterialID": None,
            "temperature": 0.7,
            "inputFormatter": None,
        }

        get_prompt_builder_response_task(team_id=1, user_id=1, data_dict=data_dict)

        mock_with_retry.assert_called_once_with(mock_llm)
        mock_create_conv.assert_called_once()
        # Verify the wrapped LLM was passed to create_conversation
        # create_conversation(prompt_str, source_material, llm) - llm is the 3rd positional arg
        call_args = mock_create_conv.call_args
        assert call_args[0][2] == mock_llm_with_retry
