import pydantic
from langchain_core.runnables import RunnableSequence
from langgraph.graph import StateGraph

from apps.pipelines.exceptions import PipelineBuildError


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

    @classmethod
    def build_runnable_from_json(cls, obj: dict) -> RunnableSequence:
        graph = cls.from_json(obj)
        return graph.build_runnable()

    def build_runnable(self) -> RunnableSequence:
        from apps.pipelines.nodes import nodes
        from apps.pipelines.nodes.base import PipelineState

        if not self.nodes:
            raise PipelineBuildError("There are no nodes in the graph")
        state_graph = StateGraph(PipelineState)
        for node in self.nodes:
            node_class = getattr(nodes, node.type)
            state_graph.add_node(node.id, node_class.get_callable(node))
        for edge in self.edges:
            state_graph.add_edge(edge.source, edge.target)

        node_ids = {n.id for n in self.nodes}
        incoming = {e.source for e in self.edges}
        outgoing = {e.target for e in self.edges}
        start = list(node_ids - outgoing)
        end = list(node_ids - incoming)
        if len(start) != 1:
            raise PipelineBuildError(f"Expected 1 start node, got {len(start)}")
        if len(end) != 1:
            raise PipelineBuildError(f"Expected 1 end node, got {len(end)}")
        state_graph.set_entry_point(start[0])
        state_graph.set_finish_point(end[0])

        compiled_graph = state_graph.compile()
        return compiled_graph
