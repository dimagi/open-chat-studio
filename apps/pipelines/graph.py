import pydantic


class Node(pydantic.BaseModel):
    id: str
    label: str
    type: str
    params: dict = {}


class Edge(pydantic.BaseModel):
    id: str
    label: str
    type: str


class PipelineGraph(pydantic.BaseModel):
    nodes: list[Node]
    edges: list[Edge]

    @classmethod
    def from_json(cls, obj: dict) -> "PipelineGraph":
        node_data = [Node(**node["data"]) for node in obj["data"]["nodes"]]
        edge_data = [Edge(**edge["data"]) for edge in obj["data"]["edges"]]
        return cls(nodes=node_data, edges=edge_data)
