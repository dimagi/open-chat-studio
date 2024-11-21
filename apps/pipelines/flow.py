"""Datamodels to hold state from react-flow for the front-end
"""

from typing import Literal

import pydantic


class FlowNodeData(pydantic.BaseModel):
    id: str
    type: str
    label: str = ""
    params: dict = {}  # Allowed in pydantic: https://docs.pydantic.dev/latest/concepts/models/#fields-with-non-hashable-default-values
    inputParams: list[dict] = []


class FlowNode(pydantic.BaseModel):
    id: str
    type: Literal["pipelineNode"] = "pipelineNode"
    position: dict = {}
    data: FlowNodeData


class FlowEdge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class Flow(pydantic.BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    errors: dict[str, dict[str, str]] = {}


class FlowPipelineData(pydantic.BaseModel):
    name: str
    data: Flow
