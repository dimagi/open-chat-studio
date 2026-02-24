from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, TypeAdapter
from pydantic_core import ValidationError

from apps.chat.conversation import COMPRESSION_MARKER
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatHistoryTypes
from apps.pipelines.nodes.history_middleware import MaxHistoryLengthHistoryMiddleware
from apps.pipelines.nodes.mixins import (
    PipelineChatHistory,
    SummarizeHistoryMiddleware,
    TruncateTokensHistoryMiddleware,
)
from apps.pipelines.nodes.nodes import (
    HistoryMixin,
    LLMResponseWithPrompt,
    OptionalInt,
    SendEmail,
    StructuredDataSchemaValidatorMixin,
)
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.service_provider_factories import EmbeddingProviderModelFactory, LlmProviderFactory


class TestStructuredDataSchemaValidatorMixin:
    class DummyModel(BaseModel, StructuredDataSchemaValidatorMixin):
        data_schema: str

    def test_valid_schema(self):
        valid_schema = '{"name": "the name of the user"}'
        model = self.DummyModel(data_schema=valid_schema)
        assert model.data_schema == valid_schema

    @pytest.mark.parametrize("schema", ['{"name": "the name of the user"', "{}", "[]"])
    def test_invalid_schema(self, schema):
        with pytest.raises(ValidationError, match="Invalid schema"):
            self.DummyModel(data_schema=schema)


class TestSendEmailInputValidation:
    @pytest.mark.parametrize(
        "recipient_list",
        [
            "test@example.com",
            "test@example.com,another@example.com",
            "test@example.com,another@example.com,yetanother@example.com",
        ],
    )
    def test_valid_recipient_list(self, recipient_list):
        model = SendEmail(
            node_id="test", django_node=None, name="email", recipient_list=recipient_list, subject="Test Subject"
        )
        assert model.recipient_list == recipient_list

    @pytest.mark.parametrize(
        "recipient_list",
        [
            "",
            "invalid-email",
            "test@example.com,invalid-email",
            "test@example.com,another@example.com,invalid-email",
        ],
    )
    def test_invalid_recipient_list(self, recipient_list):
        with pytest.raises(ValidationError, match="Invalid list of emails addresses"):
            SendEmail(name="email", recipient_list=recipient_list, subject="Test Subject")


def test_optional_int_type():
    ta = TypeAdapter(OptionalInt)
    assert ta.validate_python(1) == 1
    assert ta.validate_python(None) is None
    assert ta.validate_python("") is None

    with pytest.raises(ValidationError):
        ta.validate_python(1.2)

    with pytest.raises(ValidationError):
        ta.validate_python("test")


class TestHistoryNode(HistoryMixin):
    node_id: str = "node-id"


@pytest.fixture(autouse=True)
def mock_llm_provider_model():
    # Mock get_llm_provider_model for all tests
    with patch("apps.pipelines.nodes.mixins.get_llm_provider_model") as get_llm_provider_model:
        get_llm_provider_model.return_value = Mock(name="non-existing-model", max_token_limit=1000, deprecated=False)
        yield get_llm_provider_model


@pytest.fixture()
def history_node_factory():
    def _factory(**overrides):
        data = {
            "node_id": overrides.pop("node_id", "node-id"),
            "name": overrides.pop("name", "History Node"),
            "llm_provider_id": overrides.pop("llm_provider_id", 1),
            "llm_provider_model_id": overrides.pop("llm_provider_model_id", 2),
            "history_type": overrides.pop("history_type", PipelineChatHistoryTypes.NODE),
            "history_mode": overrides.pop("history_mode", PipelineChatHistoryModes.SUMMARIZE),
        }
        data.update(overrides)
        node = TestHistoryNode(**data)
        return node

    return _factory


class TestHistoryMixin:
    def test_get_history_uses_session_history_for_global_type(self, history_node_factory):
        """PipelineChatHistoryTypes.GLOBAL uses session history"""
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.GLOBAL,
        )
        pipeline_history = Mock()
        session = Mock(
            chat=Mock(get_langchain_messages_until_marker=Mock(return_value=["session-history"])),
            pipeline_chat_history=pipeline_history,
        )

        result = node.get_history(session)

        assert result == ["session-history"]
        session.chat.get_langchain_messages_until_marker.assert_called_once_with(
            marker=node.get_history_mode(), exclude_message_id=None
        )
        pipeline_history.get.assert_not_called()

    def test_get_history_uses_session_history_for_global_type_with_repo(self, history_node_factory):
        """PipelineChatHistoryTypes.GLOBAL uses repo when provided"""
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.GLOBAL,
        )
        repo = Mock()
        repo.get_session_messages_until_marker.return_value = ["repo-session-history"]
        session = Mock(chat=Mock())

        result = node.get_history(session, repo=repo)

        assert result == ["repo-session-history"]
        repo.get_session_messages_until_marker.assert_called_once_with(
            chat=session.chat,
            marker=node.get_history_mode(),
            exclude_message_id=None,
        )

    def test_get_history_uses_pipeline_history_when_configured(self, history_node_factory):
        """PipelineChatHistoryTypes.NODE uses pipeline history"""
        node = history_node_factory(history_type=PipelineChatHistoryTypes.NODE)
        mock_history = Mock(get_langchain_messages_until_marker=Mock(return_value=["node-history"]))
        pipeline_history = Mock(get=Mock(return_value=mock_history))
        session = Mock(chat=Mock(), pipeline_chat_history=pipeline_history)

        result = node.get_history(session)

        assert result == ["node-history"]
        pipeline_history.get.assert_called_once_with(type=PipelineChatHistoryTypes.NODE, name=node.node_id)
        mock_history.get_langchain_messages_until_marker.assert_called_once_with(node.get_history_mode())
        session.chat.get_langchain_messages_until_marker.assert_not_called()

    def test_get_history_uses_pipeline_history_with_repo(self, history_node_factory):
        """PipelineChatHistoryTypes.NODE uses repo when provided"""
        node = history_node_factory(history_type=PipelineChatHistoryTypes.NODE)
        mock_history = Mock(get_langchain_messages_until_marker=Mock(return_value=["repo-node-history"]))
        repo = Mock()
        repo.get_pipeline_chat_history.return_value = mock_history
        session = Mock()

        result = node.get_history(session, repo=repo)

        assert result == ["repo-node-history"]
        repo.get_pipeline_chat_history.assert_called_once_with(
            session=session,
            history_type=PipelineChatHistoryTypes.NODE,
            history_name=node.node_id,
        )

    def test_get_history_returns_empty_when_pipeline_history_missing(self, history_node_factory):
        node = history_node_factory(history_type=PipelineChatHistoryTypes.NODE)
        pipeline_history = Mock()
        pipeline_history.get.side_effect = PipelineChatHistory.DoesNotExist
        session = Mock(chat=Mock(), pipeline_chat_history=pipeline_history)

        assert node.get_history(session) == []
        pipeline_history.get.assert_called_once_with(type=PipelineChatHistoryTypes.NODE, name=node.node_id)

    def test_get_history_returns_empty_when_repo_returns_none(self, history_node_factory):
        """When repo returns None for pipeline history, should return empty list"""
        node = history_node_factory(history_type=PipelineChatHistoryTypes.NODE)
        repo = Mock()
        repo.get_pipeline_chat_history.return_value = None
        session = Mock()

        assert node.get_history(session, repo=repo) == []

    def test_store_compression_checkpoint_updates_metadata_with_compression_marker_global(self, history_node_factory):
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.GLOBAL,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        repo = Mock()
        node.store_compression_checkpoint(compression_marker=COMPRESSION_MARKER, checkpoint_message_id=7, repo=repo)

        repo.save_compression_checkpoint_global.assert_called_once_with(
            message_id=7,
            compression_marker=COMPRESSION_MARKER,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

    def test_store_compression_checkpoint_updates_metadata_with_compression_marker_node(self, history_node_factory):
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        repo = Mock()
        node.store_compression_checkpoint(compression_marker=COMPRESSION_MARKER, checkpoint_message_id=7, repo=repo)

        repo.save_compression_checkpoint_pipeline.assert_called_once_with(
            message_id=7,
            compression_marker=COMPRESSION_MARKER,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

    def test_store_compression_checkpoint_updates_summary_global(self, history_node_factory):
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.GLOBAL,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        repo = Mock()
        node.store_compression_checkpoint(compression_marker="a summary", checkpoint_message_id=7, repo=repo)

        repo.save_compression_checkpoint_global.assert_called_once_with(
            message_id=7,
            compression_marker="a summary",
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

    def test_store_compression_checkpoint_updates_summary_node(self, history_node_factory):
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        repo = Mock()
        node.store_compression_checkpoint(compression_marker="a summary", checkpoint_message_id=7, repo=repo)

        repo.save_compression_checkpoint_pipeline.assert_called_once_with(
            message_id=7,
            compression_marker="a summary",
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

    def test_store_compression_checkpoint_fallback_global(self, history_node_factory):
        """When repo is None, falls back to direct ORM access for global history"""
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.GLOBAL,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        with patch("apps.chat.models.ChatMessage") as mock_chat_message_class:
            mock_message = Mock(metadata={}, save=Mock())
            mock_chat_message_class.objects.get.return_value = mock_message

            node.store_compression_checkpoint(compression_marker=COMPRESSION_MARKER, checkpoint_message_id=7)

            assert mock_message.metadata["compression_marker"] == PipelineChatHistoryModes.TRUNCATE_TOKENS
            mock_message.save.assert_called_once_with(update_fields=["metadata"])

    def test_store_compression_checkpoint_fallback_node(self, history_node_factory):
        """When repo is None, falls back to direct ORM access for pipeline history"""
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
        )

        with patch("apps.pipelines.models.PipelineChatMessages") as mock_pipeline_chat_message_class:
            queryset_mock = Mock()
            mock_pipeline_chat_message_class.objects.filter.return_value = queryset_mock

            node.store_compression_checkpoint(compression_marker=COMPRESSION_MARKER, checkpoint_message_id=7)
            queryset_mock.update.assert_called_once_with(compression_marker=PipelineChatHistoryModes.TRUNCATE_TOKENS)

    def test_build_history_middleware_returns_none_when_history_disabled(self, history_node_factory):
        node = history_node_factory(history_type=PipelineChatHistoryTypes.NONE)
        middleware = node.build_history_middleware(Mock(), SystemMessage(content="system"))
        assert middleware is None

    @patch("apps.pipelines.nodes.nodes.LLMResponseMixin.get_chat_model")
    def test_build_history_middleware_uses_max_history_length(self, get_chat_model, history_node_factory):
        get_chat_model.return_value = Mock()
        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.MAX_HISTORY_LENGTH,
            max_history_length=5,
        )
        session = Mock()
        middleware = node.build_history_middleware(session, SystemMessage(content="system"))
        assert isinstance(middleware, MaxHistoryLengthHistoryMiddleware)

    @patch("apps.pipelines.nodes.mixins.LLMResponseMixin.get_chat_model")
    @patch("apps.pipelines.nodes.mixins.count_tokens_approximately")
    def test_build_history_middleware_uses_summarize_mode(
        self,
        count_tokens,
        get_chat_model,
        history_node_factory,
    ):
        get_chat_model.return_value = Mock()
        count_tokens.return_value = 50

        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.SUMMARIZE,
        )

        session = Mock()
        system_message = SystemMessage(content="system")

        middleware = node.build_history_middleware(session, system_message)

        assert isinstance(middleware, SummarizeHistoryMiddleware)
        count_tokens.assert_called_once_with([system_message])

    @patch("apps.pipelines.nodes.mixins.LLMResponseMixin.get_chat_model")
    @patch("apps.pipelines.nodes.mixins.count_tokens_approximately")
    def test_build_history_middleware_uses_truncate_tokens_mode(
        self,
        count_tokens,
        get_chat_model,
        history_node_factory,
    ):
        get_chat_model.return_value = Mock()
        count_tokens.return_value = 80

        node = history_node_factory(
            history_type=PipelineChatHistoryTypes.NODE,
            history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS,
            user_max_token_limit=200,
        )

        session = Mock()
        system_message = SystemMessage(content="system")

        middleware = node.build_history_middleware(session, system_message)

        assert isinstance(middleware, TruncateTokensHistoryMiddleware)
        count_tokens.assert_called_once_with([system_message])


@pytest.mark.django_db()
class TestLLMResponseWithPromptValidation:
    """Tests for LLMResponseWithPrompt node validation."""

    def test_openai_remote_vectorstore_limit_with_2_collections(self):
        """Test that OpenAI provider accepts exactly 2 remote vectorstores."""
        # Create OpenAI provider
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai.value.slug)

        # Create 2 remote collections with the same provider
        collection1 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)
        collection2 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)

        # Should not raise validation error with exactly 2 collections
        node = LLMResponseWithPrompt(
            node_id="test-node",
            name="Test LLM",
            django_node=None,
            llm_provider_id=openai_provider.id,
            llm_provider_model_id=1,
            prompt="You are a helpful assistant. {collection_index_summaries}",
            collection_index_ids=[collection1.id, collection2.id],
        )
        assert node.collection_index_ids == [collection1.id, collection2.id]

    def test_openai_remote_vectorstore_limit_with_3_collections(self):
        """Test that OpenAI provider rejects more than 2 remote vectorstores."""

        # Create OpenAI provider
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai.value.slug)

        # Create 3 remote collections with the same provider
        collection1 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)
        collection2 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)
        collection3 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)

        # Should raise validation error with 3 collections
        with pytest.raises(ValidationError, match="OpenAI hosted vectorstores are limited to 2 per request"):
            LLMResponseWithPrompt(
                node_id="test-node",
                name="Test LLM",
                django_node=None,
                llm_provider_id=openai_provider.id,
                llm_provider_model_id=1,
                prompt="You are a helpful assistant. {collection_index_summaries}",
                collection_index_ids=[collection1.id, collection2.id, collection3.id],
            )

    def test_non_openai_provider_allows_more_than_2_remote_vectorstores(self):
        """Test that non-OpenAI providers are not limited to 2 vectorstores."""
        # Create Anthropic provider (non-OpenAI)
        anthropic_provider = LlmProviderFactory(type=LlmProviderTypes.anthropic.value.slug)

        # Create 3 remote collections with the same provider
        collection1 = CollectionFactory(is_remote_index=True, llm_provider=anthropic_provider, is_index=True)
        collection2 = CollectionFactory(is_remote_index=True, llm_provider=anthropic_provider, is_index=True)
        collection3 = CollectionFactory(is_remote_index=True, llm_provider=anthropic_provider, is_index=True)

        # Should not raise validation error for non-OpenAI provider
        node = LLMResponseWithPrompt(
            node_id="test-node",
            name="Test LLM",
            django_node=None,
            llm_provider_id=anthropic_provider.id,
            llm_provider_model_id=1,
            prompt="You are a helpful assistant. {collection_index_summaries}",
            collection_index_ids=[collection1.id, collection2.id, collection3.id],
        )
        assert node.collection_index_ids == [collection1.id, collection2.id, collection3.id]

    def test_openai_local_vectorstores_not_limited(self):
        """Test that local (non-remote) vectorstores are not subject to the limit."""
        # Create OpenAI provider
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai.value.slug)
        embedding_model = EmbeddingProviderModelFactory()

        # Create 3 local collections (is_remote_index=False)
        collection1 = CollectionFactory(
            is_remote_index=False,
            llm_provider=openai_provider,
            is_index=True,
            summary="Collection 1",
            embedding_provider_model=embedding_model,
        )
        collection2 = CollectionFactory(
            is_remote_index=False,
            llm_provider=openai_provider,
            is_index=True,
            summary="Collection 2",
            embedding_provider_model=embedding_model,
        )
        collection3 = CollectionFactory(
            is_remote_index=False,
            llm_provider=openai_provider,
            is_index=True,
            summary="Collection 3",
            embedding_provider_model=embedding_model,
        )

        # Should not raise validation error for local indexes
        node = LLMResponseWithPrompt(
            node_id="test-node",
            name="Test LLM",
            django_node=None,
            llm_provider_id=openai_provider.id,
            llm_provider_model_id=1,
            prompt="You are a helpful assistant. {collection_index_summaries}",
            collection_index_ids=[collection1.id, collection2.id, collection3.id],
        )
        assert node.collection_index_ids == [collection1.id, collection2.id, collection3.id]

    def test_remote_vectorstores_must_have_same_llm_provider(self):
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai)
        anthropic_provider = LlmProviderFactory(type=LlmProviderTypes.anthropic)

        collection1 = CollectionFactory(is_remote_index=True, llm_provider=openai_provider, is_index=True)

        with pytest.raises(
            ValidationError, match="All remote collection indexes must use the same LLM provider as the node"
        ):
            LLMResponseWithPrompt(
                node_id="test-node",
                name="Test LLM",
                django_node=None,
                llm_provider_id=anthropic_provider.id,
                llm_provider_model_id=1,
                prompt="You are a helpful assistant. {collection_index_summaries}",
                collection_index_ids=[collection1.id],
            )

    def test_local_vectorstores_can_have_different_llm_provider(self):
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai)
        anthropic_provider = LlmProviderFactory(type=LlmProviderTypes.anthropic)

        collection1 = CollectionFactory(is_remote_index=False, llm_provider=openai_provider, is_index=True)

        node = LLMResponseWithPrompt(
            node_id="test-node",
            name="Test LLM",
            django_node=None,
            llm_provider_id=anthropic_provider.id,
            llm_provider_model_id=1,
            prompt="You are a helpful assistant.",
            collection_index_ids=[collection1.id],
        )

        assert node.collection_index_ids == [collection1.id]

    def test_local_vectorstores_must_have_summary_when_more_than_one(self):
        openai_provider = LlmProviderFactory(type=LlmProviderTypes.openai)

        collection1 = CollectionFactory(is_remote_index=False, llm_provider=openai_provider, is_index=True)
        collection2 = CollectionFactory(is_remote_index=False, llm_provider=openai_provider, is_index=True)

        with pytest.raises(ValidationError, match="collections must have a summary"):
            LLMResponseWithPrompt(
                node_id="test-node",
                name="Test LLM",
                django_node=None,
                llm_provider_id=openai_provider.id,
                llm_provider_model_id=1,
                prompt="You are a helpful assistant.",
                collection_index_ids=[collection1.id, collection2.id],
            )
