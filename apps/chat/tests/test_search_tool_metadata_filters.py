from unittest import mock

import pytest

from apps.chat.agent.tools import SearchCollectionByIdTool, SearchIndexTool, SearchToolConfig
from apps.documents.tests.retrieval_helpers import add_chunk, make_indexed_collection, unit_vector
from apps.pipelines.nodes.llm_node import _get_search_tool
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _clinic_rows(collection, file):
    add_chunk(collection, file, "clinic in Khayelitsha", unit_vector(0), metadata={"district": "Khayelitsha"})
    add_chunk(collection, file, "clinic in Mitchells Plain", unit_vector(1), metadata={"district": "Mitchells Plain"})


def _llm_node(collection_index_ids, metadata_filters):
    provider = LlmProviderFactory.create()
    model = LlmProviderModelFactory.create(team=provider.team)
    prompt = "You are a helpful assistant."
    if len(collection_index_ids) > 1:
        prompt += " {collection_index_summaries}"
    node = LLMResponseWithPrompt(
        node_id="test-node",
        name="Test LLM",
        django_node=None,
        llm_provider_id=provider.id,
        llm_provider_model_id=model.id,
        prompt=prompt,
        collection_index_ids=collection_index_ids,
        metadata_filters=metadata_filters,
    )
    node._repo = ORMRepository()
    return node


@pytest.mark.django_db()
class TestSearchToolsApplyMetadataFilters:
    def test_single_index_tool_returns_only_matching_rows(self):
        collection, file = make_indexed_collection()
        _clinic_rows(collection, file)
        config = SearchToolConfig(index_id=collection.id, metadata_filters={"district": "Khayelitsha"})

        result = SearchIndexTool(search_config=config).action(query="clinic")

        assert "clinic in Khayelitsha" in result
        assert "Mitchells Plain" not in result

    def test_multi_index_tool_returns_only_matching_rows(self):
        collection, file = make_indexed_collection()
        _clinic_rows(collection, file)
        tool = SearchCollectionByIdTool(
            allowed_collection_ids=[collection.id], metadata_filters={"district": "Khayelitsha"}
        )

        result = tool.action(collection_index_id=collection.id, query="clinic")

        assert "clinic in Khayelitsha" in result
        assert "Mitchells Plain" not in result

    def test_no_results_message_says_a_filter_was_applied(self):
        collection, file = make_indexed_collection()
        _clinic_rows(collection, file)
        config = SearchToolConfig(index_id=collection.id, metadata_filters={"district": "Gugulethu"})

        result = SearchIndexTool(search_config=config).action(query="clinic")

        assert "did not return any results" in result
        assert "district = Gugulethu" in result


@pytest.mark.django_db()
class TestLlmNodeSearchToolFilters:
    def test_a_single_local_index_searches_with_the_node_filters(self):
        collection, file = make_indexed_collection(summary="Clinics")
        _clinic_rows(collection, file)
        node = _llm_node([collection.id], [{"key": "district", "value": "Khayelitsha"}])

        result = _get_search_tool(node).action(query="clinic")

        assert "clinic in Khayelitsha" in result
        assert "Mitchells Plain" not in result

    def test_several_local_indexes_search_with_the_node_filters(self):
        collection, file = make_indexed_collection(summary="Clinics")
        _clinic_rows(collection, file)
        other, _ = make_indexed_collection(
            team=collection.team, summary="Other", embedding_provider_model=collection.embedding_provider_model
        )
        node = _llm_node([collection.id, other.id], [{"key": "district", "value": "Khayelitsha"}])

        result = _get_search_tool(node).action(collection_index_id=collection.id, query="clinic")

        assert "clinic in Khayelitsha" in result
        assert "Mitchells Plain" not in result

    def test_no_filters_leave_the_search_unfiltered(self):
        collection, file = make_indexed_collection(summary="Clinics")
        _clinic_rows(collection, file)
        node = _llm_node([collection.id], [])

        with mock.patch.object(type(collection), "get_query_vector", return_value=unit_vector(0)):
            result = _get_search_tool(node).action(query="clinic")

        assert "clinic in Khayelitsha" in result
        assert "clinic in Mitchells Plain" in result
