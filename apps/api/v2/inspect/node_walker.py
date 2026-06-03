"""Signal-driven pipeline node walker for the inspect projection (ADR-0025, design D7).

The walker does not hand-maintain a node-type -> reference-field table. For each node it looks up
the pydantic class from the registry, iterates its model fields, and classifies each field's UI
signal (its ``options_source`` or ``widget``) against an explicit registry. Fields whose signal
maps to a resource type are recorded as references to embed inline; everything else goes verbatim
into ``params``.

A **completeness guard** test (``tests/test_completeness_guard.py``) asserts that every
``OptionsSource`` and every ``Widgets`` value is classified as either "embeds resource X" or
"explicitly not a resource", so introducing a new, unclassified signal breaks CI rather than
silently omitting a resource reference.
"""

import dataclasses

from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.nodes.base import OptionsSource, UiSchema, Widgets

# Resource-kind keys — shared vocabulary across the walker, collector, and serializer registry.
SOURCE_MATERIAL = "source_material"
ASSISTANT = "assistant"
CUSTOM_ACTION = "custom_action"
COLLECTION = "collection"
SYNTHETIC_VOICE = "synthetic_voice"
VOICE_PROVIDER = "voice_provider"
LLM_PROVIDER = "llm_provider"
LLM_PROVIDER_MODEL = "llm_provider_model"

# ── Signal registry ────────────────────────────────────────────────────────────────────────────
# Each entry maps an ``OptionsSource`` that denotes a resource reference to
# ``(payload_key, resource_kind, is_list)``.
OPTIONS_SOURCE_RESOURCES: dict[OptionsSource, tuple[str, str, bool]] = {
    OptionsSource.source_material: ("source_material", SOURCE_MATERIAL, False),
    OptionsSource.assistant: ("assistant", ASSISTANT, False),
    OptionsSource.custom_actions: ("custom_actions", CUSTOM_ACTION, True),
    OptionsSource.collection: ("media_collection", COLLECTION, False),
    OptionsSource.collection_index: ("indexed_collections", COLLECTION, True),
    # Forward-compat: these enum values exist but no current node field uses them as an
    # options_source (voice is signalled by the voice_widget widget in ``walk_node``). Classified so the
    # completeness guard stays green and a future field using them embeds the right resource.
    OptionsSource.voice_provider_id: ("voice", VOICE_PROVIDER, False),
    OptionsSource.synthetic_voice_id: ("voice", SYNTHETIC_VOICE, False),
}

# Widget signals carry the reference when there is no options_source: ``llm_provider_model`` marks
# the LLM provider/model pair; ``voice_widget`` marks the synthetic-voice field (handled in
# ``walk_node``). Non-resource signals are enumerated in ``tests/test_completeness_guard.py``,
# which asserts every ``OptionsSource`` and ``Widgets`` value is classified one way or the other.

# Field names that pair with the ``llm_provider_model`` widget (mixins.LLMResponseMixin). The
# widget sits on ``llm_provider_id``; ``llm_provider_model_id`` carries no signal of its own.
_LLM_PROVIDER_FIELD = "llm_provider_id"
_LLM_PROVIDER_MODEL_FIELD = "llm_provider_model_id"


# ── Reference value objects ──────────────────────────────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class SingleRef:
    kind: str
    id: int | None


@dataclasses.dataclass(frozen=True)
class ListRef:
    kind: str
    ids: list[int]


@dataclasses.dataclass(frozen=True)
class CustomActionsRef:
    """Per-node custom-action selection: ``(action_id, [operation_ids])`` pairs in first-seen
    order. The operation ids are kept so only the node-selected operations are rendered, not the
    action's full operation set."""

    selections: list[tuple[int, list[str]]]


@dataclasses.dataclass(frozen=True)
class LlmRef:
    provider_id: int | None
    model_id: int | None


@dataclasses.dataclass(frozen=True)
class VoiceRef:
    synthetic_voice_id: int | None


@dataclasses.dataclass
class NodeWalkResult:
    flow_id: str
    type: str
    label: str
    params: dict
    refs: dict[str, object]  # payload_key -> ref value object


@dataclasses.dataclass
class PipelineWalk:
    id: int
    name: str
    version_number: int
    graph: dict
    nodes: list[NodeWalkResult]
    resource_refs: dict[str, set[int]]  # resource_kind -> ids to batch-load


def _signal(field_info) -> tuple[Widgets | None, OptionsSource | None]:
    """Extract ``(widget, options_source)`` from a pydantic field's ``json_schema_extra``.

    Most node fields use a :class:`UiSchema` instance; a few use a plain dict (e.g. the node
    ``name`` field). Both are handled.
    """
    extra = field_info.json_schema_extra
    if isinstance(extra, UiSchema):
        return extra.widget, extra.options_source
    if isinstance(extra, dict):
        return extra.get("ui:widget"), extra.get("ui:optionsSource")
    return None, None


def _node_class(node_type: str):
    return getattr(pipeline_nodes, node_type, None)


def _parse_custom_actions(value) -> list[tuple[int, list[str]]]:
    """``custom_actions`` values are ``"{action_id}:{operation_id}"`` strings. Group the selected
    operation ids per custom action (preserving first-seen order)."""
    selections: dict[int, list[str]] = {}
    for entry in value or []:
        action_part, _, operation_id = str(entry).partition(":")
        try:
            action_id = int(action_part)
        except (TypeError, ValueError):
            continue
        operation_ids = selections.setdefault(action_id, [])
        if operation_id and operation_id not in operation_ids:
            operation_ids.append(operation_id)
    return list(selections.items())


def _coerce_int_list(value) -> list[int]:
    items = value if isinstance(value, list) else [value]
    out: list[int] = []
    for item in items:
        if item is None:
            continue
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def walk_node(node) -> NodeWalkResult:
    """Split a pipeline node's stored params into resource references and plain config.

    Classifies each field of the node's pydantic class by its UI signal (widget / options_source):
    fields that signal a resource become typed refs (``SingleRef``/``ListRef``/``LlmRef``/``VoiceRef``)
    keyed by payload key; everything else stays verbatim in ``params``.
    """
    node_class = _node_class(node.type)
    fields = node_class.model_fields if node_class is not None else {}
    refs: dict[str, object] = {}
    consumed: set[str] = set()

    for field_name, field_info in fields.items():
        if field_name in consumed:
            continue
        widget, options_source = _signal(field_info)

        if widget == Widgets.llm_provider_model:
            refs["llm"] = LlmRef(
                provider_id=node.params.get(_LLM_PROVIDER_FIELD),
                model_id=node.params.get(_LLM_PROVIDER_MODEL_FIELD),
            )
            consumed.update({_LLM_PROVIDER_FIELD, _LLM_PROVIDER_MODEL_FIELD})
            continue

        if widget == Widgets.voice_widget:
            refs["voice"] = VoiceRef(synthetic_voice_id=node.params.get(field_name))
            consumed.add(field_name)
            continue

        if options_source in OPTIONS_SOURCE_RESOURCES:
            payload_key, kind, is_list = OPTIONS_SOURCE_RESOURCES[options_source]
            value = node.params.get(field_name)
            if options_source == OptionsSource.custom_actions:
                refs[payload_key] = CustomActionsRef(_parse_custom_actions(value))
            elif is_list:
                refs[payload_key] = ListRef(kind, _coerce_int_list(value))
            else:
                refs[payload_key] = SingleRef(kind, value)
            consumed.add(field_name)

    # ``params`` reflects the stored config verbatim, minus consumed reference fields and the
    # redundant node ``name`` (the human label is carried separately as ``label``).
    params = {key: value for key, value in node.params.items() if key not in consumed and key != "name"}
    return NodeWalkResult(flow_id=node.flow_id, type=node.type, label=node.label, params=params, refs=refs)


def accumulate_refs(refs: dict[str, object], into: dict[str, set[int]]) -> None:
    """Merge a node/event's refs into the ``resource_kind -> ids`` batch-load accumulator."""
    for ref in refs.values():
        if isinstance(ref, SingleRef):
            if ref.id:
                into.setdefault(ref.kind, set()).add(int(ref.id))
        elif isinstance(ref, ListRef):
            for rid in ref.ids:
                into.setdefault(ref.kind, set()).add(int(rid))
        elif isinstance(ref, CustomActionsRef):
            for action_id, _operation_ids in ref.selections:
                into.setdefault(CUSTOM_ACTION, set()).add(int(action_id))
        elif isinstance(ref, LlmRef):
            if ref.provider_id:
                into.setdefault(LLM_PROVIDER, set()).add(int(ref.provider_id))
            if ref.model_id:
                into.setdefault(LLM_PROVIDER_MODEL, set()).add(int(ref.model_id))
        elif isinstance(ref, VoiceRef):
            if ref.synthetic_voice_id:
                into.setdefault(SYNTHETIC_VOICE, set()).add(int(ref.synthetic_voice_id))


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Topology only: nodes as ``{flow_id, type, label}`` (from DB columns), edges with positions
    stripped and handle keys normalised."""
    nodes = [{"flow_id": node.flow_id, "type": node.type, "label": node.label} for node in node_list]
    edges = []
    for edge in (pipeline_data or {}).get("edges", []):
        edges.append(
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
                "source_handle": edge.get("sourceHandle"),
                "target_handle": edge.get("targetHandle"),
            }
        )
    return {"nodes": nodes, "edges": edges}


def walk_pipeline(pipeline) -> PipelineWalk:
    """Walk every node of ``pipeline`` once, producing the graph digest, per-node detail, and the
    accumulated ``resource_kind -> ids`` map for the collector to batch-load."""
    # Order by id (creation order) so the serialized node list is deterministic.
    node_list = list(pipeline.node_set.order_by("id"))
    results = [walk_node(node) for node in node_list]
    resource_refs: dict[str, set[int]] = {}
    for result in results:
        accumulate_refs(result.refs, resource_refs)
    return PipelineWalk(
        id=pipeline.id,
        name=pipeline.name,
        version_number=pipeline.version_number,
        graph=graph_digest(node_list, pipeline.data),
        nodes=results,
        resource_refs=resource_refs,
    )
