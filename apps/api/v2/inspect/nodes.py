import enum

from apps.api.v2.utils import parse_custom_actions
from apps.pipelines.nodes import nodes as pipeline_nodes


class ResourceKind(enum.StrEnum):
    """The kinds of resource a pipeline node can reference. A ``StrEnum``, so members double as
    their string values."""

    SOURCE_MATERIAL = "source_material"
    ASSISTANT = "assistant"
    CUSTOM_ACTION = "custom_action"
    COLLECTION = "collection"
    SYNTHETIC_VOICE = "synthetic_voice"
    VOICE_PROVIDER = "voice_provider"
    LLM_PROVIDER = "llm_provider"
    LLM_PROVIDER_MODEL = "llm_provider_model"

    def iter_raw_ids(self, value):
        """Yield the resource ids held in a node param ``value`` for this kind.

        Custom-action values are ``"{action_id}:{operation_id}"`` strings, so the action ids are
        parsed out; every other kind is a single id or a list of them.
        """
        if self is ResourceKind.CUSTOM_ACTION:
            for action_id, _operation_ids in parse_custom_actions(value):
                yield action_id
        elif isinstance(value, list):
            yield from value
        else:
            yield value


# Each resource param field (the real pydantic field name, across all node types) -> the kind/model
# it loads. Keys are the flat set of "which params are resources"; values are how to load each.
# list-vs-scalar is sniffed from the value at load time; how fields group into render keys is the
# serializer's job (e.g. ``llm`` draws from the two llm_provider* fields, both collection fields
# share the COLLECTION kind so they batch-load as one query).
RESOURCE_PARAM_FIELDS: dict[str, ResourceKind] = {
    "llm_provider_id": ResourceKind.LLM_PROVIDER,
    "llm_provider_model_id": ResourceKind.LLM_PROVIDER_MODEL,
    "synthetic_voice_id": ResourceKind.SYNTHETIC_VOICE,
    "source_material_id": ResourceKind.SOURCE_MATERIAL,
    "assistant_id": ResourceKind.ASSISTANT,
    "custom_actions": ResourceKind.CUSTOM_ACTION,
    "collection_id": ResourceKind.COLLECTION,
    "collection_index_ids": ResourceKind.COLLECTION,
}


def node_class_for(node_type: str):
    """Look up a node's pydantic class by type name, or return ``None`` if the type is unknown."""
    return getattr(pipeline_nodes, node_type, None)


def node_render_order(node) -> int:
    """Sort key that puts the start node first and the end node last, leaving the rest in order."""
    return {"StartNode": 0, "EndNode": 2}.get(node.type, 1)


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Build a lightweight view of the pipeline's shape.

    Returns just the nodes (each as ``{flow_id, type, label}``) and the edges between them, with
    canvas positions removed and the edge handle keys renamed to snake_case.
    """
    nodes = [{"flow_id": node.flow_id, "type": node.type, "label": node.label} for node in node_list]
    edges = [
        {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("sourceHandle"),
            "target_handle": edge.get("targetHandle"),
        }
        for edge in (pipeline_data or {}).get("edges", [])
    ]
    return {"nodes": nodes, "edges": edges}
