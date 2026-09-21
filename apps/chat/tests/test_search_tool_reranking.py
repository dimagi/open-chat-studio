from unittest import mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from waffle.testutils import override_flag

from apps.chat.agent.tools import (
    RERANK_CONTEXT_MESSAGE_COUNT,
    SearchIndexTool,
    SearchToolConfig,
    _recent_conversation_context,
)
from apps.service_providers.models import LlmProviderTypes
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory

HYBRID_SEARCH_FLAG = "flag_hybrid_search"


@pytest.mark.django_db()
class TestRerankConversationContext:
    """Search tools pass the LLM's already-loaded graph history to the reranker."""

    def _rerankable_collection(self, team):
        """A collection with the provider and model required for reranking."""
        return CollectionFactory.create(
            team=team,
            reranker_provider=LlmProviderFactory.create(
                team=team, type=str(LlmProviderTypes.voyage), config={"voyage_api_key": "test-voyage-key"}
            ),
        )

    def test_returns_no_context_when_reranking_is_inactive(self, team):
        collection = CollectionFactory.create(team=team)
        graph_state = {"messages": [HumanMessage("hello")]}

        assert _recent_conversation_context(collection, graph_state) is None

    def test_returns_no_context_when_reranking_is_unconfigured(self, team):
        collection = CollectionFactory.create(team=team, reranker_provider=None)
        graph_state = {"messages": [HumanMessage("hello")]}

        with override_flag(HYBRID_SEARCH_FLAG, active=True):
            assert _recent_conversation_context(collection, graph_state) is None

    def test_returns_no_context_without_messages(self, team):
        collection = self._rerankable_collection(team)

        with override_flag(HYBRID_SEARCH_FLAG, active=True):
            assert _recent_conversation_context(collection, {}) is None

    def test_returns_the_recent_turns_oldest_first(self, team):
        collection = self._rerankable_collection(team)
        graph_state = {
            "messages": [
                HumanMessage("tell me about the permit"),
                AIMessage("which permit do you mean?"),
                HumanMessage("the building one"),
            ]
        }

        with override_flag(HYBRID_SEARCH_FLAG, active=True):
            context = _recent_conversation_context(collection, graph_state)

        assert context == (
            "user: tell me about the permit\nassistant: which permit do you mean?\nuser: the building one"
        )

    def test_keeps_only_the_most_recent_turns(self, team):
        """The context is clipped again downstream, but sending the whole history to be clipped
        would mean loading it first."""
        overflow = 4
        collection = self._rerankable_collection(team)
        graph_state = {
            "messages": [HumanMessage(f"turn {index}") for index in range(RERANK_CONTEXT_MESSAGE_COUNT + overflow)]
        }

        with override_flag(HYBRID_SEARCH_FLAG, active=True):
            context = _recent_conversation_context(collection, graph_state)

        assert context is not None
        turns = context.splitlines()
        assert len(turns) == RERANK_CONTEXT_MESSAGE_COUNT
        assert turns[0] == f"user: turn {overflow}"
        assert turns[-1] == f"user: turn {RERANK_CONTEXT_MESSAGE_COUNT + overflow - 1}"

    def test_excludes_non_conversation_and_empty_messages(self, team):
        collection = self._rerankable_collection(team)
        graph_state = {
            "messages": [
                SystemMessage("you are a helpful assistant"),
                HumanMessage("the building one"),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "call-1"}]),
                ToolMessage("old search results", tool_call_id="call-1"),
            ]
        }

        with override_flag(HYBRID_SEARCH_FLAG, active=True):
            context = _recent_conversation_context(collection, graph_state)

        assert context == "user: the building one"

    def test_the_tool_passes_the_context_to_retrieval(self, team, local_index_manager_mock):
        collection = self._rerankable_collection(team)
        graph_state = {"messages": [HumanMessage("tell me about the permit")]}
        search_config = SearchToolConfig(index_id=collection.id, max_results=2)

        with mock.patch("apps.chat.agent.tools.search_collection", return_value=[]) as search:
            with override_flag(HYBRID_SEARCH_FLAG, active=True):
                SearchIndexTool(search_config=search_config).action(query="how much", graph_state=graph_state)

        assert search.call_args.kwargs["context"] == "user: tell me about the permit"

    def test_the_tool_passes_no_context_when_reranking_is_inactive(self, team, local_index_manager_mock):
        collection = CollectionFactory.create(team=team)
        graph_state = {"messages": [HumanMessage("tell me about the permit")]}
        search_config = SearchToolConfig(index_id=collection.id, max_results=2)

        with mock.patch("apps.chat.agent.tools.search_collection", return_value=[]) as search:
            with override_flag(HYBRID_SEARCH_FLAG, active=False):
                SearchIndexTool(search_config=search_config).action(query="how much", graph_state=graph_state)

        assert search.call_args.kwargs["context"] is None
