from types import SimpleNamespace

from apps.api.v2.inspect.node_walker import (
    CustomActionsRef,
    ListRef,
    LlmRef,
    ResourceKind,
    SingleRef,
    VoiceRef,
    accumulate_refs,
    graph_digest,
    walk_node,
)


def _node(node_type, params, flow_id="n1", label="A node"):
    return SimpleNamespace(type=node_type, params=params, flow_id=flow_id, label=label)


def test_llm_node_splits_refs_and_params():
    node = _node(
        "LLMResponseWithPrompt",
        {
            "name": "Classify",
            "llm_provider_id": 5,
            "llm_provider_model_id": 9,
            "source_material_id": 7,
            "collection_id": 21,
            "collection_index_ids": [33, 34],
            "custom_actions": ["12:complete_session", "12:other_op", "15:foo"],
            "synthetic_voice_id": 4,
            "prompt": "You are helpful",
            "tools": [],
            "history_type": "global",
        },
    )
    result = walk_node(node)

    # Reference fields are extracted, not left in params.
    assert result.refs["llm"] == LlmRef(provider_id=5, model_id=9)
    assert result.refs["source_material"] == SingleRef(ResourceKind.SOURCE_MATERIAL, 7)
    assert result.refs["media_collection"] == SingleRef(ResourceKind.COLLECTION, 21)
    assert result.refs["indexed_collections"] == ListRef(ResourceKind.COLLECTION, [33, 34])
    # "12:op1", "12:op2", "15:foo" -> per-action operation selections, grouped in first-seen order
    assert result.refs["custom_actions"] == CustomActionsRef([(12, ["complete_session", "other_op"]), (15, ["foo"])])
    assert result.refs["voice"] == VoiceRef(synthetic_voice_id=4)

    # Non-reference fields stay verbatim; name is dropped (carried as label); ref fields removed.
    assert result.params == {"prompt": "You are helpful", "tools": [], "history_type": "global"}
    assert result.label == "A node"
    assert "llm_provider_id" not in result.params
    assert "synthetic_voice_id" not in result.params


def test_assistant_node_reference():
    node = _node("AssistantNode", {"assistant_id": 3, "citations_enabled": True})
    result = walk_node(node)
    assert result.refs["assistant"] == SingleRef(ResourceKind.ASSISTANT, 3)
    assert result.params == {"citations_enabled": True}


def test_node_with_no_references():
    node = _node("RenderTemplate", {"template_string": "{{ input }}"})
    result = walk_node(node)
    assert result.refs == {}
    assert result.params == {"template_string": "{{ input }}"}


def test_unknown_node_type_falls_back_to_params():
    node = _node("TotallyMadeUpNode", {"foo": "bar"})
    result = walk_node(node)
    assert result.refs == {}
    assert result.params == {"foo": "bar"}


def test_unset_references_are_omitted():
    node = _node(
        "LLMResponseWithPrompt", {"llm_provider_id": 5, "llm_provider_model_id": 9, "source_material_id": None}
    )
    result = walk_node(node)
    assert result.refs["source_material"] == SingleRef(ResourceKind.SOURCE_MATERIAL, None)
    # accumulate skips None ids so nothing is batch-loaded for it
    acc: dict = {}
    accumulate_refs(result.refs, acc)
    assert ResourceKind.SOURCE_MATERIAL not in acc
    assert acc[ResourceKind.LLM_PROVIDER] == {5}
    assert acc[ResourceKind.LLM_PROVIDER_MODEL] == {9}


def test_accumulate_refs_collects_ids_by_kind():
    acc: dict = {}
    accumulate_refs({"a": SingleRef(ResourceKind.SOURCE_MATERIAL, 7)}, acc)
    accumulate_refs({"b": ListRef(ResourceKind.COLLECTION, [33, 34]), "c": LlmRef(5, 9), "v": VoiceRef(4)}, acc)
    accumulate_refs({"d": SingleRef(ResourceKind.SOURCE_MATERIAL, 8)}, acc)
    accumulate_refs({"e": CustomActionsRef([(12, ["op_a"]), (15, [])])}, acc)
    assert acc[ResourceKind.SOURCE_MATERIAL] == {7, 8}
    assert acc[ResourceKind.COLLECTION] == {33, 34}
    assert acc[ResourceKind.LLM_PROVIDER] == {5}
    assert acc[ResourceKind.LLM_PROVIDER_MODEL] == {9}
    assert acc[ResourceKind.SYNTHETIC_VOICE] == {4}
    assert acc[ResourceKind.CUSTOM_ACTION] == {12, 15}


def test_graph_digest_normalises_handles_and_strips_positions():
    nodes = [
        SimpleNamespace(flow_id="start-1", type="StartNode", label="Start"),
        SimpleNamespace(flow_id="end-1", type="EndNode", label="End"),
    ]
    data = {
        "nodes": [{"id": "start-1", "position": {"x": 1, "y": 2}}],
        "edges": [{"source": "start-1", "target": "end-1", "sourceHandle": "output", "targetHandle": "input"}],
    }
    digest = graph_digest(nodes, data)
    assert digest["nodes"] == [
        {"flow_id": "start-1", "type": "StartNode", "label": "Start"},
        {"flow_id": "end-1", "type": "EndNode", "label": "End"},
    ]
    assert digest["edges"] == [
        {"source": "start-1", "target": "end-1", "source_handle": "output", "target_handle": "input"}
    ]
