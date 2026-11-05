from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.pipelines.models import Node
from apps.pipelines.tests.utils import content_flow_node
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory

LLM_PARAMS = {"name": "llm1", "prompt": "helpful", "history_type": "global"}


def _write_llm_node(pipeline, **overrides):
    entry = {"node_type": "LLMResponseWithPrompt", "label": "LLM", "params": LLM_PARAMS, "position": {"x": 1, "y": 1}}
    entry.update(overrides)
    node_data = {node.flow_id: None for node in pipeline.node_set.all()}
    node_data["llm1"] = content_flow_node("llm1", **entry)
    pipeline.update_nodes_from_data(node_data)


@pytest.mark.django_db()
class TestResourceSyncOnlyWhenContentChanged:
    """The FK/M2M/custom-action mirror depends on type and params alone, so a save that changes
    neither (a move or a relabel, the common autosave) skips re-deriving it."""

    @pytest.mark.parametrize(
        ("overrides", "synced"),
        [
            pytest.param({"position": {"x": 2, "y": 2}}, False, id="move-only"),
            pytest.param({"label": "Renamed"}, False, id="relabel"),
            pytest.param({"params": {**LLM_PARAMS, "prompt": "changed"}}, True, id="params-changed"),
            pytest.param({"node_type": "RenderTemplate", "params": {"template_string": "x"}}, True, id="type-changed"),
        ],
    )
    def test_an_existing_node_is_resynced_only_when_type_or_params_change(self, overrides, synced):
        pipeline = PipelineFactory.create()
        _write_llm_node(pipeline)

        with mock.patch.object(Node, "update_from_params", autospec=True) as update_from_params:
            _write_llm_node(pipeline, **overrides)

        assert update_from_params.called is synced

    def test_a_new_node_is_synced(self):
        pipeline = PipelineFactory.create()

        with mock.patch.object(Node, "update_from_params", autospec=True) as update_from_params:
            _write_llm_node(pipeline)

        assert [call.args[0].flow_id for call in update_from_params.call_args_list] == ["llm1"]


@pytest.mark.django_db()
class TestCollectionIndexSync:
    def test_matching_prefetched_rows_skip_the_write(self):
        index = CollectionFactory.create()
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={"collection_index_ids": [index.id]})
        node.collection_indexes.set([index])
        node = Node.objects.prefetch_related("collection_indexes").get(pk=node.pk)

        with CaptureQueriesContext(connection) as captured:
            node._sync_collection_indexes(node.params)

        assert captured.captured_queries == []

    def test_changed_ids_are_written_through_the_prefetch(self):
        old, new = CollectionFactory.create(), CollectionFactory.create()
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={"collection_index_ids": [old.id]})
        node.collection_indexes.set([old])
        node = Node.objects.prefetch_related("collection_indexes").get(pk=node.pk)

        node._sync_collection_indexes({"collection_index_ids": [new.id]})

        assert set(node.collection_indexes.values_list("id", flat=True)) == {new.id}
