"""Build-state reporting for a pipeline: the errors report and the advisory maps beside it.

``Pipeline.validate()`` returns a complete :class:`~apps.pipelines.exceptions.ErrorReport`, which
this passes through unchanged::

    {"node": {<node_id>: {<field>: <message>}}, "edge": [<edge_id>], "pipeline": [<message>]}

``pipeline_valid`` is exactly "all three buckets empty" — nothing more.

Validation never flags an unwired node or branch (the build only checks reachable nodes), so
:func:`unwired_handles` reports those separately as an advisory "what still needs wiring" map, and
:func:`deprecated_models` reports the models on their way out the same way. Neither blocks anything.
"""

from apps.pipelines.exceptions import has_errors
from apps.pipelines.flow import Flow
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.llm_service.default_models import get_deprecated_models
from apps.service_providers.models import LlmProviderModel


def pipeline_build_state(pipeline: Pipeline) -> dict:
    """``pipeline_valid`` + ``errors`` + the advisory ``unwired_handles`` and ``deprecated_models``."""
    errors = pipeline.validate()
    return {
        "pipeline_valid": not has_errors(errors),
        "errors": errors,
        "unwired_handles": unwired_handles(pipeline),
        "deprecated_models": deprecated_models(pipeline),
    }


def deprecated_models(pipeline: Pipeline) -> dict:
    """The advisory ``{node_id: {model, replacement}}`` map of nodes pointing at a deprecated model.
    ``replacement`` is the model the team should move to, or ``None`` where none is declared.
    """
    nodes_by_model_id = {}
    for node in pipeline.node_set.all():
        model_id = _referenced_model_id(node)
        if model_id is not None:
            nodes_by_model_id.setdefault(model_id, []).append(node.flow_id)
    if not nodes_by_model_id:
        return {}

    warnings = {}
    replacements = get_deprecated_models()
    deprecated = LlmProviderModel.objects.for_team(pipeline.team_id).filter(id__in=nodes_by_model_id, deprecated=True)
    for model in deprecated.values_list("id", "type", "name", named=True):
        warning = {"model": model.name, "replacement": replacements.get((model.type, model.name))}
        for flow_id in nodes_by_model_id[model.id]:
            warnings[flow_id] = warning
    return warnings


def _referenced_model_id(node: Node) -> int | None:
    """The LLM model id a node's params name, or None if it names none."""
    model_id = (node.params or {}).get("llm_provider_model_id")
    try:
        return int(model_id)
    except (TypeError, ValueError):
        return None


def unwired_handles(pipeline: Pipeline) -> dict:
    """The advisory ``{node_id: [{handle, label}]}`` map of handles with no edge.

    Covers both sides: output handles with no outgoing edge and the implicit ``input`` handle when
    a node has no incoming edge — so an off-graph island shows up in full. Start's input and End's
    output are excluded (they have none).
    """
    # The empty defaults keep a pipeline with no stored graph yet from failing Flow validation.
    edges = Flow.model_validate({"nodes": [], "edges": [], **(pipeline.data or {})}).edges
    # Wiredness is judged purely from the stored edges: an edge pointing at a handle its source no
    # longer offers still marks that (source, handle) pair "wired" here — the stranded edge itself
    # is the errors.edge bucket's concern (and, like validation, only surfaces for reachable nodes).
    wired_outputs = {(edge.source, edge.source_handle_name) for edge in edges}
    wired_inputs = {edge.target for edge in edges}

    unwired = {}
    for node in pipeline.node_set.all():
        if dangling := _dangling_handles(node, wired_inputs, wired_outputs):
            unwired[node.flow_id] = dangling
    return unwired


def _dangling_handles(node: Node, wired_inputs: set[str], wired_outputs: set[tuple[str, str]]) -> list[dict]:
    """One node's unwired handles: the implicit input plus any output with no edge."""
    unwired_inputs = [] if node.flow_id in wired_inputs else node.node_type.input_handles()
    dangling = [{"handle": handle, "label": None} for handle in unwired_inputs]
    for handle in node.output_handles():
        if (node.flow_id, handle["handle"]) not in wired_outputs:
            dangling.append(handle)
    return dangling
