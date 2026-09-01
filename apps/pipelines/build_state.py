"""Build-state reporting for a pipeline: the errors report and the advisory unwired-handles map.

``Pipeline.validate()`` returns a complete :class:`~apps.pipelines.exceptions.ErrorReport`, which
this passes through unchanged::

    {"node": {<node_id>: {<field>: <message>}}, "edge": [<edge_id>], "pipeline": [<message>]}

``pipeline_valid`` is exactly "all three buckets empty" — nothing more.

Validation never flags an unwired node or branch (the build only checks reachable nodes), so
:func:`unwired_handles` reports those separately as an advisory "what still needs wiring" map. It
never blocks anything.
"""

import pydantic

from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME
from apps.pipelines.exceptions import PipelineNodeBuildError, has_errors
from apps.pipelines.flow import Flow
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.nodes.base import PipelineRouterNode, resolve_node_class
from apps.pipelines.nodes.nodes import EndNode, StartNode


def pipeline_build_state(pipeline: Pipeline) -> dict:
    """``pipeline_valid`` + ``errors`` + advisory ``unwired_handles`` for a pipeline."""
    errors = pipeline.validate()
    return {
        "pipeline_valid": not has_errors(errors),
        "errors": errors,
        "unwired_handles": unwired_handles(pipeline),
    }


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
    wired_outputs = {(edge.source, edge.sourceHandle or STANDARD_OUTPUT_NAME) for edge in edges}
    wired_inputs = {edge.target for edge in edges}

    unwired = {}
    for node in pipeline.node_set.all():
        if dangling := _dangling_handles(node, wired_inputs, wired_outputs):
            unwired[node.flow_id] = dangling
    return unwired


def _dangling_handles(node: Node, wired_inputs: set[str], wired_outputs: set[tuple[str, str]]) -> list[dict]:
    """One node's unwired handles: the implicit input plus any output with no edge."""
    dangling = [
        {"handle": handle, "label": None} for handle in input_handles(node.type) if node.flow_id not in wired_inputs
    ]
    for handle in node_output_handles(node):
        if (node.flow_id, handle["handle"]) not in wired_outputs:
            dangling.append(handle)
    return dangling


def input_handles(node_type: str) -> list[str]:
    """The input handles a node of this type accepts an edge on.

    Every type has exactly one, implicit, ``input`` handle -- bar Start, which has none: nothing runs
    before the beginning of the pipeline, and the UI builder draws no target handle on it. A list
    rather than a flag so a caller reads inputs and outputs the same way.
    """
    return [] if node_type == StartNode.__name__ else [STANDARD_INPUT_NAME]


def node_output_handles(node: Node) -> list[dict]:
    """The output handles a :class:`~apps.pipelines.models.Node` offers, as ``{handle, label}``."""
    return output_handles(node.type, node.params or {}, node.flow_id, django_node=node)


def output_handles(node_type: str, params: dict, node_id: str, django_node: Node | None = None) -> list[dict]:
    """The output handles a node of this type and these params offers, as ``{handle, label}``.

    Routers get one handle per branch from ``get_output_map()`` (``output_0``, ``output_1``, …,
    labelled with the branch keyword); plain nodes get the single standard output with no label;
    End has no outputs.

    Takes the params rather than only a stored :class:`~apps.pipelines.models.Node` so a caller
    holding an unwritten edit can ask what the node *would* offer. ``django_node`` is what the
    row-backed caller passes for full validation; without it a router falls back to the unvalidated
    path below, which is enough because no router's branches depend on its row.
    """
    if node_type == EndNode.__name__:
        return []
    node_class = resolve_node_class(node_type)
    if node_class is None:
        # A type naming no node class (removed since, or never one): validation reports it; we can't
        # know its handles.
        return []
    if issubclass(node_class, PipelineRouterNode):
        output_map = _router_output_map(node_class, params, node_id, django_node)
        return [{"handle": handle, "label": label} for handle, label in output_map.items()]
    return [{"handle": STANDARD_OUTPUT_NAME, "label": None}]


def _router_output_map(
    node_class: type[PipelineRouterNode], params: dict, node_id: str, django_node: Node | None
) -> dict:
    """A router's handle -> branch-label map, tolerant of invalid params.

    Prefer full validation so every field normalization applies — a router type whose
    ``get_output_map()`` depends on validated/derived fields stays correct at the cost of one
    redundant validation per read. An incrementally-built router can be invalid in ways unrelated
    to its branches (a missing required field, a broken resource reference raising
    ``PipelineNodeBuildError``), and must still report its handles, so fall back to an unvalidated
    instance with the keywords upper-cased to match ``RouterMixin.ensure_keywords_are_uppercase``.
    """
    try:
        instance = node_class.model_validate({**params, "node_id": node_id, "django_node": django_node})
    except (pydantic.ValidationError, PipelineNodeBuildError):
        fallback = dict(params)
        if isinstance(fallback.get("keywords"), list):
            fallback["keywords"] = [str(keyword).upper() for keyword in fallback["keywords"]]
        instance = node_class.model_construct(**fallback)
    return instance.get_output_map()
