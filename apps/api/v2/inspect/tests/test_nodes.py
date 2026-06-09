"""Unit tests for the node-type resource registry (no database)."""

from types import SimpleNamespace

from apps.api.v2.inspect.nodes import (
    RESOURCE_PARAM_FIELDS,
    graph_digest,
    node_class_for,
    node_render_order,
)
from apps.pipelines.nodes import nodes as pipeline_nodes


def test_node_class_for_resolves_known_type():
    assert node_class_for("AssistantNode") is pipeline_nodes.AssistantNode


def test_node_class_for_unknown_type_is_none():
    assert node_class_for("NoSuchNode") is None


def test_resource_param_fields_are_real_node_fields():
    """Every registered resource param field is a real pydantic field on at least one node type, so
    a typo in the registry can't go unnoticed."""
    declared_anywhere = set()
    for name in dir(pipeline_nodes):
        model_fields = getattr(getattr(pipeline_nodes, name), "model_fields", None)
        if isinstance(model_fields, dict):
            declared_anywhere |= set(model_fields)
    missing = RESOURCE_PARAM_FIELDS.keys() - declared_anywhere
    assert not missing, f"RESOURCE_PARAM_FIELDS not declared on any node type: {sorted(missing)}"


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
