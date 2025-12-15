from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from apps.chat.conversation import COMPRESSION_MARKER
from apps.pipelines.nodes.history_middleware import (
    BaseNodeHistoryMiddleware,
    MaxHistoryLengthHistoryMiddleware,
    SummarizeHistoryMiddleware,
    TruncateTokensHistoryMiddleware,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import FakeLlmSimpleTokenCount


@pytest.fixture()
def experiment_session(db):
    return ExperimentSessionFactory()


@pytest.fixture()
def mock_node():
    """Create a mock node with required methods for middleware."""
    node = Mock()
    node.get_chat_model.return_value = FakeLlmSimpleTokenCount(responses=["Test summary"])
    node.get_history.return_value = []
    node.node_id = "test_node_id"
    node.get_history_mode.return_value = "summarize"
    node.store_compression_checkpoint = Mock()
    return node


class TestBaseNodeHistoryMiddleware:
    """Tests for BaseNodeHistoryMiddleware._find_latest_message_db_id"""

    def test_find_latest_message_db_id_success(self, experiment_session, mock_node):
        """Test that _find_latest_message_db_id returns the correct id with valid messages."""
        middleware = BaseNodeHistoryMiddleware(
            session=experiment_session,
            node=mock_node,
            trigger=("messages", 10),  # doesn't matter for this test
            keep=("messages", 5),  # doesn't matter for this test
        )

        messages = [
            HumanMessage(content="First message", additional_kwargs={"id": 1}),
            AIMessage(content="Second message", additional_kwargs={"id": 2}),
            HumanMessage(content="Third message", additional_kwargs={"id": 3}),
            HumanMessage(content="New user message"),  # This one doesn't have an id
        ]

        result = middleware._find_latest_message_db_id(messages)
        assert result == 3

    def test_find_latest_message_db_id_no_id(self, experiment_session, mock_node):
        """Test that _find_latest_message_db_id returns None when no message has an id."""
        middleware = BaseNodeHistoryMiddleware(
            session=experiment_session,
            node=mock_node,
            trigger=("messages", 10),
            keep=("messages", 5),
        )

        messages = [
            HumanMessage(content="First message"),
            AIMessage(content="Second message"),
            HumanMessage(content="Third message"),
        ]

        result = middleware._find_latest_message_db_id(messages)
        assert result is None


class TestMaxHistoryLengthHistoryMiddleware:
    @patch.object(MaxHistoryLengthHistoryMiddleware, "_should_summarize")
    def test_before_model_with_summarization(self, mock_should_summarize, experiment_session, mock_node):
        """Test that before_model returns correct structure with RemoveMessage and limited messages."""
        mock_should_summarize.return_value = True
        keep_value = 4

        middleware = MaxHistoryLengthHistoryMiddleware(
            session=experiment_session,
            node=mock_node,
            max_history_length=keep_value,
        )

        # Generate some messages
        messages = []
        for i in range(0, 20, 2):
            messages.append(HumanMessage(content=f"Message {i}", additional_kwargs={"id": i}))
            messages.append(AIMessage(content=f"Response {i + 1}", additional_kwargs={"id": i + 1}))

        messages.append(HumanMessage(content="The user query"))
        state = {"messages": messages}
        runtime = Mock()

        result = middleware.before_model(state, runtime)

        # RemoveMessage + the number of kept messages
        assert len(result["messages"]) == 5, f"Expected {keep_value + 1} messages, got {len(result['messages'])}"
        assert isinstance(result["messages"][0], RemoveMessage)

        # We expect the last 4 messages to be kept
        assert isinstance(result["messages"][1], AIMessage)
        assert result["messages"][1].additional_kwargs["id"] == 17
        assert isinstance(result["messages"][2], HumanMessage)
        assert result["messages"][2].additional_kwargs["id"] == 18
        assert isinstance(result["messages"][3], AIMessage)
        assert result["messages"][3].additional_kwargs["id"] == 19
        assert isinstance(result["messages"][4], HumanMessage)
        assert result["messages"][4].additional_kwargs.get("id") is None


class TestTruncateTokensHistoryMiddleware:
    @patch.object(TruncateTokensHistoryMiddleware, "_should_summarize")
    @patch.object(TruncateTokensHistoryMiddleware, "_determine_cutoff_index")
    def test_before_model_with_compression_marker(
        self, mock_cutoff_index, mock_should_summarize, experiment_session, mock_node
    ):
        """
        Test that before_model returns RemoveMessage and no summary message, but uses the compression marker
        (COMPRESSION_MARKER) when storing the checkpoint.
        """
        mock_cutoff_index.return_value = 8
        mock_should_summarize.return_value = True
        token_limit = 100

        middleware = TruncateTokensHistoryMiddleware(
            session=experiment_session,
            node=mock_node,
            token_limit=token_limit,
        )

        # Generate some messages
        messages = []
        for i in range(0, 10, 2):
            messages.append(HumanMessage(content=f"Message {i}", additional_kwargs={"id": i}))
            messages.append(AIMessage(content=f"Response {i + 1}", additional_kwargs={"id": i + 1}))

        messages.append(HumanMessage(content="The user query"))
        state = {"messages": messages}
        runtime = Mock()

        result = middleware.before_model(state, runtime)

        # First message should be RemoveMessage
        assert result is not None, "Expected a result from before_model"
        assert isinstance(result["messages"][0], RemoveMessage)
        # Rest should be human/ai messages
        for msg in result["messages"][2:]:
            assert isinstance(msg, HumanMessage | AIMessage)
            assert msg.content != COMPRESSION_MARKER, "Unexpected compression marker in messages"

        mock_node.store_compression_checkpoint.assert_called_with(
            compression_marker=COMPRESSION_MARKER, checkpoint_message_id=9
        )


class TestSummarizeHistoryMiddleware:
    @patch.object(SummarizeHistoryMiddleware, "_should_summarize")
    @patch.object(SummarizeHistoryMiddleware, "_determine_cutoff_index")
    @patch.object(SummarizeHistoryMiddleware, "_create_summary")
    def test_before_model_with_summary(
        self, mock_create_summary, _determine_cutoff_index, mock_should_summarize, experiment_session, mock_node
    ):
        """Test that before_model returns RemoveMessage and a summary message."""
        mock_should_summarize.return_value = True
        _determine_cutoff_index.return_value = 5
        mock_create_summary.return_value = "This is a test summary"
        token_limit = 100

        middleware = SummarizeHistoryMiddleware(
            session=experiment_session,
            node=mock_node,
            token_limit=token_limit,
        )

        # Generate some messages
        messages = []
        for i in range(0, 10, 2):
            messages.append(HumanMessage(content=f"Message {i}", additional_kwargs={"id": i}))
            messages.append(AIMessage(content=f"Response {i + 1}", additional_kwargs={"id": i + 1}))

        messages.append(HumanMessage(content="The user query"))

        state = {"messages": messages}
        runtime = Mock()

        result = middleware.before_model(state, runtime)

        assert result is not None, "Expected a result from before_model"
        # First message should be RemoveMessage
        assert isinstance(result["messages"][0], RemoveMessage)

        # Second message should contain the summary
        assert "This is a test summary" in result["messages"][1].content, "Summary is missing or incorrect"

        # Rest should be human/ai messages
        for msg in result["messages"][2:]:
            assert isinstance(msg, HumanMessage | AIMessage)

        mock_node.store_compression_checkpoint.assert_called_once()
