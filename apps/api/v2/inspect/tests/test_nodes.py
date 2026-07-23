"""Unit tests for the node graph helpers and the resource-param registry (no database)."""

from types import SimpleNamespace

from apps.api.v2.inspect.nodes import graph_digest, node_render_order
from apps.api.v2.inspect.serializers import InspectNodeSerializer
from apps.pipelines.nodes import nodes as pipeline_nodes


def test_resource_param_fields_are_real_node_fields():
    """Every stripped resource param key is a real pydantic field on at least one node type, so a
    typo in the render-key -> backing-param map can't go unnoticed."""
    declared_anywhere = set()
    for name in dir(pipeline_nodes):
        model_fields = getattr(getattr(pipeline_nodes, name), "model_fields", None)
        if isinstance(model_fields, dict):
            declared_anywhere |= set(model_fields)
    missing = InspectNodeSerializer._RESOURCE_PARAM_KEYS - declared_anywhere
    assert not missing, f"resource param keys not declared on any node type: {sorted(missing)}"


def test_node_render_order_pins_start_first_end_last():
    start = SimpleNamespace(type="StartNode")
    end = SimpleNamespace(type="EndNode")
    middle = SimpleNamespace(type="LLMResponseWithPrompt")
    assert node_render_order(start) < node_render_order(middle) < node_render_order(end)


def test_graph_digest_strips_positions_and_normalises_handles():
    nodes = [SimpleNamespace(flow_id="a", type="StartNode", label="Start")]
    data = {"edges": [{"id": "e1", "source": "a", "target": "b", "sourceHandle": "out", "targetHandle": "in", "x": 1}]}
    assert graph_digest(nodes, data) == {
        "nodes": [{"flow_id": "a", "type": "StartNode", "label": "Start"}],
        "edges": [{"id": "e1", "source": "a", "target": "b", "source_handle": "out", "target_handle": "in"}],
    }


def test_graph_digest_handles_missing_pipeline_data():
    assert graph_digest([], None) == {"nodes": [], "edges": []}
