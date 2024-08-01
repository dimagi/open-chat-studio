"""Datamodels to hold state from react-flow for the front-end
"""

import pydantic


class FlowNode(pydantic.BaseModel):
    id: str
    type: str
    position: dict
    data: dict


class FlowEdge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class Flow(pydantic.BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    viewport: dict


class FlowPipelineData(pydantic.BaseModel):
    name: str
    data: Flow
