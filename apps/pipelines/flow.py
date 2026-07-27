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
    data: FlowNodeData


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
