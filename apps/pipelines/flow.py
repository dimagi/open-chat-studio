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
    # Keep unknown top-level keys (viewport) so a stored graph survives the round trip.
    model_config = pydantic.ConfigDict(extra="allow")

    # Stored ``Pipeline.data`` is layout-only and carries no ``nodes`` (ADR-0048), so nodes
    # default to empty; full nodes still arrive on the wire and are rebuilt for reads.
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge]
    errors: dict[str, dict[str, str]] = Field(default_factory=dict)


class FlowWithoutNodes(pydantic.BaseModel):
    """The shape of a stored ``Pipeline.data``: a graph minus its nodes (ADR-0048)."""

    model_config = pydantic.ConfigDict(extra="allow")

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


def split_flow_data(flow: Flow) -> tuple[FlowWithoutNodes, dict[str, FlowNode | None]]:
    """Split a graph into its layout (no ``nodes`` — ADR-0048) and the complete node
    membership: content-carrying nodes map to themselves, content-less ones to ``None``
    (membership only, so their row must already exist).
    """
    node_data: dict[str, FlowNode | None] = {node.id: node if node.data else None for node in flow.nodes}
    return FlowWithoutNodes(**flow.model_dump(exclude={"nodes"})), node_data


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


class WireFlow(Flow):
    """A full-graph save payload. Unlike stored layout-only data, the wire format must
    state the node list explicitly: an omitted ``nodes`` is a malformed payload, not an
    empty graph — treating it as empty would delete every node row on save."""

    # Unknown keys from a client are dropped rather than persisted.
    model_config = pydantic.ConfigDict(extra="ignore")

    nodes: list[FlowNode]


class FlowPipelineData(pydantic.BaseModel):
    name: str
    data: WireFlow
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
