"""Node-type resource registry for the chatbot inspect projection (ADR-0025).

This is the single source of truth for *which* node-param fields are resource references and the
payload key they render under. It is consumed by both the id-collection traversal
(``resources.iter_resource_refs``) and the node serializer's rendering, and is pinned by the
two-layer completeness guard test.
"""

import dataclasses
import enum

from apps.pipelines.nodes import nodes as pipeline_nodes


class ResourceKind(enum.StrEnum):
    """Resource-kind keys shared across the registry and the fetcher. ``StrEnum`` so members stay
    interchangeable with their string values."""

    SOURCE_MATERIAL = "source_material"
    ASSISTANT = "assistant"
    CUSTOM_ACTION = "custom_action"
    COLLECTION = "collection"
    SYNTHETIC_VOICE = "synthetic_voice"
    VOICE_PROVIDER = "voice_provider"
    LLM_PROVIDER = "llm_provider"
    LLM_PROVIDER_MODEL = "llm_provider_model"


@dataclasses.dataclass(frozen=True)
class ResourceField:
    """One inspect payload key: the node-param field name(s) it consumes, the resource kind it
    loads, and whether it renders a list. Rich enough to model composites like ``llm``, which
    consumes two fields."""

    consumes: frozenset[str]
    kind: ResourceKind
    is_list: bool


# payload_key -> ResourceField. Field names are the real pydantic field names on the node classes.
RESOURCE_FIELDS: dict[str, ResourceField] = {
    "llm": ResourceField(
        frozenset({"llm_provider_id", "llm_provider_model_id"}), ResourceKind.LLM_PROVIDER_MODEL, False
    ),
    "voice": ResourceField(frozenset({"synthetic_voice_id"}), ResourceKind.SYNTHETIC_VOICE, False),
    "source_material": ResourceField(frozenset({"source_material_id"}), ResourceKind.SOURCE_MATERIAL, False),
    "assistant": ResourceField(frozenset({"assistant_id"}), ResourceKind.ASSISTANT, False),
    "custom_actions": ResourceField(frozenset({"custom_actions"}), ResourceKind.CUSTOM_ACTION, True),
    "media_collection": ResourceField(frozenset({"collection_id"}), ResourceKind.COLLECTION, False),
    "indexed_collections": ResourceField(frozenset({"collection_index_ids"}), ResourceKind.COLLECTION, True),
}

# The payload keys, in render order — used by the node serializer's ``to_representation``.
RESOURCE_KEYS = tuple(RESOURCE_FIELDS)


def node_class_for(node_type: str):
    """Resolve a node's pydantic class from the registry, or ``None`` for an unknown type."""
    return getattr(pipeline_nodes, node_type, None)


def declared_resource_keys(node_class) -> list[str]:
    """Payload keys whose source field(s) the node type declares.

    Declared if ANY consumed field is present (e.g. ``llm`` is declared off either of its two
    fields). Returns keys in ``RESOURCE_FIELDS`` order. ``node_class`` may be ``None`` (unknown
    type)."""
    fields = set(node_class.model_fields) if node_class is not None else set()
    return [key for key, rf in RESOURCE_FIELDS.items() if rf.consumes & fields]


def node_render_order(node) -> int:
    """Pin the start node first and the end node last; everything else keeps creation order."""
    return {"StartNode": 0, "EndNode": 2}.get(node.type, 1)


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Topology only: nodes as ``{flow_id, type, label}`` (DB columns), edges with positions
    stripped and handle keys normalised."""
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
