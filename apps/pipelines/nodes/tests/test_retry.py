from unittest.mock import MagicMock, patch


class TestLLMResponseRetry:
    @patch("apps.pipelines.nodes.nodes.with_llm_retry")
    def test_llm_response_uses_retry(self, mock_with_retry):
        """Verify LLMResponse wraps LLM with retry."""
        from apps.pipelines.nodes.nodes import LLMResponse

        mock_llm = MagicMock()
        mock_llm_with_retry = MagicMock()
        mock_llm_with_retry.invoke.return_value.content = "response"
        mock_with_retry.return_value = mock_llm_with_retry

        node = MagicMock(spec=LLMResponse)
        node.get_chat_model = MagicMock(return_value=mock_llm)
        node._config = {}
        node.name = "test"
        node.node_id = "test-id"

        # Call the actual _process method
        state = {"last_node_input": "test input"}
        LLMResponse._process(node, state)

        mock_with_retry.assert_called_once_with(mock_llm)


class TestExtractStructuredDataRetry:
    @patch("apps.pipelines.nodes.mixins.with_llm_retry")
    def test_extraction_chain_uses_retry(self, mock_with_retry):
        """Verify extraction chain wraps LLM with retry."""
        from apps.pipelines.nodes.mixins import ExtractStructuredDataNodeMixin

        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_with_retry.return_value = MagicMock()

        # Create a base class that provides get_chat_model (like LLMResponseMixin)
        class BaseLLMNode:
            def get_chat_model(self):
                return mock_llm

        # Mixin comes first in MRO, so super().get_chat_model() resolves to BaseLLMNode
        class MockNode(ExtractStructuredDataNodeMixin, BaseLLMNode):
            pass

        node = MockNode()
        node.extraction_chain(MagicMock(), "reference")

        mock_llm.with_structured_output.assert_called_once()
        mock_with_retry.assert_called_once_with(mock_structured)
