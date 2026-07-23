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
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    errors: dict[str, dict[str, str]] = Field(default_factory=dict)


#: The only node keys persisted in ``Pipeline.data`` — everything else is node content
#: owned by the ``Node`` model (see ADR-0046).
LAYOUT_NODE_KEYS = ("id", "type", "position")


def split_flow_data(data: dict) -> tuple[dict, dict[str, dict]]:
    """Split a full react-flow graph into layout-only data and per-node content.

    Returns ``(layout_data, node_data)`` where ``layout_data`` keeps only
    ``LAYOUT_NODE_KEYS`` per node (edges and unknown top-level keys pass through) and
    ``node_data`` maps flow_id to ``{"type", "label", "params"}`` for every node that
    carried an embedded ``data`` key. Layout-only input yields an empty ``node_data``.
    The input is not mutated.
    """
    if "nodes" not in data:
        return {**data}, {}

    node_data = {}
    layout_nodes = []
    for node in data["nodes"]:
        content = node.get("data")
        if content:
            node_data[node["id"]] = {
                "type": content["type"],
                "label": content.get("label", ""),
                "params": content.get("params", {}),
            }
        layout_nodes.append({key: node[key] for key in LAYOUT_NODE_KEYS if key in node})
    return {**data, "nodes": layout_nodes}, node_data


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
