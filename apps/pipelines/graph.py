import pydantic


class Node(pydantic.BaseModel):
    id: str
    label: str
    type: str
    params: dict = {}


class Edge(pydantic.BaseModel):
    id: str
    source: str
    target: str


class PipelineGraph(pydantic.BaseModel):
    nodes: list[Node]
    edges: list[Edge]

    @classmethod
    def from_json(cls, obj: dict) -> "PipelineGraph":
        node_data = [Node(**node["data"]) for node in obj["nodes"]]
        edge_data = [Edge(**edge) for edge in obj["edges"]]
        return cls(nodes=node_data, edges=edge_data)
