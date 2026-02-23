"""Datamodels to hold state from react-flow for the front-end"""

from typing import Literal

import pydantic
from pydantic import Field

from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME


class FlowNodeData(pydantic.BaseModel):
    id: str
    type: str
    label: str = ""
    params: dict = {}  # Allowed in pydantic: https://docs.pydantic.dev/latest/concepts/models/#fields-with-non-hashable-default-values


class FlowNode(pydantic.BaseModel):
    id: str
    type: Literal["pipelineNode", "startNode", "endNode"] = "pipelineNode"
    position: dict = {}
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
    errors: dict[str, dict[str, str]] = {}


class FlowPipelineData(pydantic.BaseModel):
    name: str
    data: Flow
    experiment_name: str | None = Field(default=None, min_length=1)
