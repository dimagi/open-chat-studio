"""Envelope-serializer behaviour that the schema relies on: absent ref keys are omitted (not
nulled), params pass through verbatim, trigger-action params are nested under ``params``."""

from apps.api.v2.inspect.serializers import (
    GraphSerializer,
    InspectNodeSerializer,
    InspectTriggerActionSerializer,
)


def test_node_refs_omitted_when_absent():
    node = {"flow_id": "n1", "type": "RenderTemplate", "label": "T", "params": {"template_string": "{{ input }}"}}
    data = InspectNodeSerializer(node).data
    assert data == {
        "flow_id": "n1",
        "type": "RenderTemplate",
        "label": "T",
        "params": {"template_string": "{{ input }}"},
    }


def test_node_empty_list_refs_render_as_empty_lists():
    node = {
        "flow_id": "n1",
        "type": "LLMResponseWithPrompt",
        "label": "A",
        "params": {},
        "custom_actions": [],
        "indexed_collections": [],
    }
    data = InspectNodeSerializer(node).data
    assert data["custom_actions"] == []
    assert data["indexed_collections"] == []


def test_trigger_action_params_nested_not_spread():
    action = {"type": "send_message_to_bot", "params": {"message_to_bot": "hi"}}
    data = InspectTriggerActionSerializer(action).data
    # params stay nested; the pipeline key is omitted for non-pipeline actions
    assert data == {"type": "send_message_to_bot", "params": {"message_to_bot": "hi"}}


def test_graph_edge_handles_are_nullable():
    graph = {
        "nodes": [{"flow_id": "s", "type": "StartNode", "label": "Start"}],
        "edges": [{"source": "s", "target": "e", "source_handle": None, "target_handle": None}],
    }
    data = GraphSerializer(graph).data
    assert data["edges"][0]["source_handle"] is None
