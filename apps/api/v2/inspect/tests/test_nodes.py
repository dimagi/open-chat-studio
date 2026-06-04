"""Pure unit tests for the node-type resource registry (no DB)."""

from types import SimpleNamespace

from apps.api.v2.inspect.nodes import (
    RESOURCE_FIELDS,
    ResourceKind,
    declared_resource_keys,
    graph_digest,
    node_class_for,
    node_render_order,
)
from apps.pipelines.nodes import nodes as pipeline_nodes


def test_node_class_for_resolves_known_type():
    assert node_class_for("AssistantNode") is pipeline_nodes.AssistantNode


def test_node_class_for_unknown_type_is_none():
    assert node_class_for("NoSuchNode") is None


def test_llm_response_with_prompt_declares_all_its_resource_keys():
    keys = set(declared_resource_keys(pipeline_nodes.LLMResponseWithPrompt))
    assert keys == {"llm", "voice", "source_material", "media_collection", "indexed_collections", "custom_actions"}
    assert "assistant" not in keys


def test_assistant_node_declares_only_assistant():
    assert declared_resource_keys(pipeline_nodes.AssistantNode) == ["assistant"]


def test_router_node_declares_only_llm():
    assert declared_resource_keys(pipeline_nodes.RouterNode) == ["llm"]


def test_start_node_declares_no_resource_keys():
    assert declared_resource_keys(pipeline_nodes.StartNode) == []


def test_declared_resource_keys_of_none_is_empty():
    assert declared_resource_keys(None) == []


def test_voice_declared_when_only_synthetic_voice_field_present():
    """The multi-source ``voice`` key is declared if ANY of its consumed fields is on the node
    type — here only ``synthetic_voice_id`` exists, ``voice_provider_id`` never does."""
    stub = SimpleNamespace(model_fields={"synthetic_voice_id": object()})
    assert "voice" in declared_resource_keys(stub)


def test_resource_fields_consumes_are_real_field_sets():
    assert RESOURCE_FIELDS["llm"].consumes == frozenset({"llm_provider_id", "llm_provider_model_id"})
    assert RESOURCE_FIELDS["indexed_collections"].consumes == frozenset({"collection_index_ids"})
    assert RESOURCE_FIELDS["indexed_collections"].is_list is True
    assert RESOURCE_FIELDS["custom_actions"].kind is ResourceKind.CUSTOM_ACTION


def test_node_render_order_pins_start_first_end_last():
    start = SimpleNamespace(type="StartNode")
    end = SimpleNamespace(type="EndNode")
    middle = SimpleNamespace(type="LLMResponseWithPrompt")
    assert node_render_order(start) < node_render_order(middle) < node_render_order(end)


def test_graph_digest_strips_positions_and_normalises_handles():
    nodes = [SimpleNamespace(flow_id="a", type="StartNode", label="Start")]
    data = {"edges": [{"source": "a", "target": "b", "sourceHandle": "out", "targetHandle": "in", "x": 1}]}
    assert graph_digest(nodes, data) == {
        "nodes": [{"flow_id": "a", "type": "StartNode", "label": "Start"}],
        "edges": [{"source": "a", "target": "b", "source_handle": "out", "target_handle": "in"}],
    }


def test_graph_digest_handles_missing_pipeline_data():
    assert graph_digest([], None) == {"nodes": [], "edges": []}
