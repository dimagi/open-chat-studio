"""Build-state reporting for a pipeline: the normalized errors report and the advisory
unwired-handles map.

``Pipeline.validate()`` returns partial shapes — ``{}``, ``{"node": {...}}``, or a
``PipelineBuildError.to_json()`` graph error whose ``edge`` may be null and whose node errors use
the ``"root"`` field sentinel. :func:`normalize_errors` folds all of them into one always-present
three-bucket report::

    {"node": {<node_id>: {<field>: <message>}}, "edge": [<edge_id>], "pipeline": <message or None>}

``pipeline_valid`` is exactly "all three buckets empty" — nothing more.

Validation never flags an unwired node or branch (the build only checks reachable nodes), so
:func:`unwired_handles` reports those separately as an advisory "what still needs wiring" map. It
never blocks anything.
"""

import pydantic

from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.nodes.base import PipelineRouterNode
from apps.pipelines.nodes.nodes import EndNode, StartNode


def pipeline_build_state(pipeline) -> dict:
    """``pipeline_valid`` + normalized ``errors`` + advisory ``unwired_handles`` for a pipeline."""
    errors = normalize_errors(pipeline.validate())
    return {
        "pipeline_valid": not (errors["node"] or errors["edge"] or errors["pipeline"]),
        "errors": errors,
        "unwired_handles": unwired_handles(pipeline),
    }


def normalize_errors(raw: dict | None) -> dict:
    """Normalize a ``Pipeline.validate()`` result into the three-bucket errors report."""
    raw = raw or {}
    return {
        "node": {node_id: dict(fields) for node_id, fields in (raw.get("node") or {}).items()},
        "edge": list(raw.get("edge") or []),
        "pipeline": raw.get("pipeline"),
    }


def unwired_handles(pipeline) -> dict:
    """The advisory ``{node_id: [{handle, label}]}`` map of handles with no edge.

    Covers both sides: output handles with no outgoing edge and the implicit ``input`` handle when
    a node has no incoming edge — so an off-graph island shows up in full. Start's input and End's
    output are excluded (they have none).
    """
    edges = (pipeline.data or {}).get("edges", [])
    # Wiredness is judged purely from the stored edges: an edge pointing at a handle its source no
    # longer offers still marks that (source, handle) pair "wired" here — the stranded edge itself
    # is the errors.edge bucket's concern (and, like validation, only surfaces for reachable nodes).
    wired_outputs = {(edge.get("source"), edge.get("sourceHandle") or STANDARD_OUTPUT_NAME) for edge in edges}
    wired_inputs = {edge.get("target") for edge in edges}

    unwired = {}
    for node in pipeline.node_set.all():
        if dangling := _dangling_handles(node, wired_inputs, wired_outputs):
            unwired[node.flow_id] = dangling
    return unwired


def _dangling_handles(node, wired_inputs: set, wired_outputs: set) -> list[dict]:
    """One node's unwired handles: the implicit input plus any output with no edge."""
    dangling = []
    if node.type != StartNode.__name__ and node.flow_id not in wired_inputs:
        dangling.append({"handle": STANDARD_INPUT_NAME, "label": None})
    for handle in node_output_handles(node):
        if (node.flow_id, handle["handle"]) not in wired_outputs:
            dangling.append(handle)
    return dangling


def node_output_handles(node) -> list[dict]:
    """The output handles a :class:`~apps.pipelines.models.Node` offers, as ``{handle, label}``.

    Routers get one handle per branch from ``get_output_map()`` (``output_0``, ``output_1``, …,
    labelled with the branch keyword); plain nodes get the single standard output with no label;
    End has no outputs.
    """
    if node.type == EndNode.__name__:
        return []
    node_class = getattr(pipeline_nodes, node.type, None)
    if node_class is None:
        # A node whose class was removed: validation reports it; we can't know its handles.
        return []
    if issubclass(node_class, PipelineRouterNode):
        output_map = _router_output_map(node_class, node)
        return [{"handle": handle, "label": label} for handle, label in output_map.items()]
    return [{"handle": STANDARD_OUTPUT_NAME, "label": None}]


def _router_output_map(node_class, node) -> dict:
    """A router's handle -> branch-label map, tolerant of invalid params.

    Prefer full validation so every field normalization applies — a router type whose
    ``get_output_map()`` depends on validated/derived fields stays correct at the cost of one
    redundant validation per read. An incrementally-built router can be invalid in ways unrelated
    to its branches (a missing required field, a broken resource reference raising
    ``PipelineNodeBuildError``), and must still report its handles, so fall back to an unvalidated
    instance with the keywords upper-cased to match ``RouterMixin.ensure_keywords_are_uppercase``.
    """
    params = node.params or {}
    try:
        instance = node_class.model_validate({**params, "node_id": node.flow_id, "django_node": node})
    except (pydantic.ValidationError, PipelineNodeBuildError):
        fallback = dict(params)
        if isinstance(fallback.get("keywords"), list):
            fallback["keywords"] = [str(keyword).upper() for keyword in fallback["keywords"]]
        instance = node_class.model_construct(**fallback)
    return instance.get_output_map()
