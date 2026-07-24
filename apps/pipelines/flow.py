"""Datamodels to hold state from react-flow for the front-end"""

from typing import Literal

import pydantic
from pydantic import Field

from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME


class FlowNodeData(pydantic.BaseModel):
    id: str
    type: str
    label: str = ""
    params: dict = Field(default_factory=dict)


class FlowNode(pydantic.BaseModel):
    id: str
    type: Literal["pipelineNode", "startNode", "endNode"] = "pipelineNode"
    position: dict = Field(default_factory=dict)
    # Persisted pipeline data is layout-only, so stored nodes have no content. Full nodes
    # (data populated) appear on the wire and in Pipeline.flow_data output.
    data: FlowNodeData | None = None


class FlowEdge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = STANDARD_OUTPUT_NAME
    targetHandle: str | None = STANDARD_INPUT_NAME


class Flow(pydantic.BaseModel):
    # Stored ``Pipeline.data`` is layout-only and carries no ``nodes`` (ADR-0047), so nodes
    # default to empty; full nodes still arrive on the wire and are rebuilt for reads.
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge]
    errors: dict[str, dict[str, str]] = Field(default_factory=dict)


#: React-flow node types. ``Node.type`` (the pipeline node class name) maps onto one of
#: these for the editor; the reserved start/end classes get their own types.
REACT_FLOW_START_TYPE = "startNode"
REACT_FLOW_END_TYPE = "endNode"
REACT_FLOW_NODE_TYPE = "pipelineNode"


def react_flow_node_type(node_type: str) -> str:
    """Map a ``Node.type`` (pipeline node class name) onto its react-flow node type."""
    if node_type == "StartNode":
        return REACT_FLOW_START_TYPE
    if node_type == "EndNode":
        return REACT_FLOW_END_TYPE
    return REACT_FLOW_NODE_TYPE


def split_flow_data(data: dict) -> tuple[dict, dict[str, dict | None]]:
    """Split a full react-flow graph into layout-only data and per-node content.

    Returns ``(layout_data, node_data)`` where ``layout_data`` drops the ``nodes`` key
    entirely (edges, viewport and unknown top-level keys pass through) — ``Pipeline.data``
    holds no node information beyond edges (ADR-0047). ``node_data`` is the complete graph
    membership: it has an entry for *every* node in the input. Nodes carrying an embedded
    ``data`` key map to ``{"type", "label", "params", "position"}`` (content and layout are
    owned by the ``Node`` rows); nodes without one map to ``None`` (membership only — the
    row must already exist, otherwise ``update_nodes_from_data`` raises). The input is not
    mutated.
    """
    if "nodes" not in data:
        return {**data}, {}

    node_data: dict[str, dict | None] = {}
    for node in data["nodes"]:
        content = node.get("data")
        if content:
            node_data[node["id"]] = {
                "type": content["type"],
                "label": content.get("label", ""),
                "params": content.get("params", {}),
                "position": node.get("position"),
            }
        else:
            node_data[node["id"]] = None
    return {key: value for key, value in data.items() if key != "nodes"}, node_data


def node_position_fields(position) -> dict:
    """Map a react-flow position onto the ``Node`` position column values.

    Returns ``{"position_x", "position_y"}``, or ``{}`` when the position is missing or
    malformed (raw import files bypass wire validation) so the caller skips the write.
    """
    x = position.get("x") if isinstance(position, dict) else None
    y = position.get("y") if isinstance(position, dict) else None
    if isinstance(x, int | float) and isinstance(y, int | float):
        return {"position_x": x, "position_y": y}
    return {}


class FlowPipelineData(pydantic.BaseModel):
    name: str
    data: Flow
    experiment_name: str | None = Field(default=None, min_length=1)


class NodeDiff(pydantic.BaseModel):
    """Describes changes to nodes within a graph diff."""

    add: list[FlowNode] = Field(default_factory=list)
    update: list[FlowNode] = Field(default_factory=list)
    delete: list[str] = Field(default_factory=list)


class EdgeDiff(pydantic.BaseModel):
    """Describes changes to edges within a graph diff."""

    add: list[FlowEdge] = Field(default_factory=list)
    update: list[FlowEdge] = Field(default_factory=list)
    delete: list[str] = Field(default_factory=list)


class PipelineDiffPayload(pydantic.BaseModel):
    """Semantic graph diff for incremental pipeline saves.

    Rules:
    - add contains complete FlowNode / FlowEdge objects.
    - update contains complete FlowNode / FlowEdge objects.
    - delete contains only string IDs.
    - Deleting a node must also remove all connected edges (enforced by backend).
    """

    base_revision: int
    nodes: NodeDiff = Field(default_factory=NodeDiff)
    edges: EdgeDiff = Field(default_factory=EdgeDiff)
    name: str | None = None
