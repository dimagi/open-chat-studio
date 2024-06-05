import pydantic


class Node(pydantic.BaseModel):
    id: str
    type: str
    position: dict
    data: dict


class Edge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class Flow(pydantic.BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    viewport: dict


class PipelineData(pydantic.BaseModel):
    name: str
    data: Flow
