from collections import defaultdict
from functools import partial

import pydantic
from langchain_core.runnables import RunnableSequence
from langgraph.graph import StateGraph
from pydantic_core import ValidationError

from apps.pipelines.const import FALSE_NODE, TRUE_NODE
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.models import Pipeline


class Node(pydantic.BaseModel):
    id: str
    label: str
    type: str
    params: dict = {}


class Edge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None

    def is_conditional_edge(self):
        return self.sourceHandle not in ["input", "output"]


class PipelineGraph(pydantic.BaseModel):
    nodes: list[Node]
    edges: list[Edge]

    @classmethod
    def build_runnable_from_pipeline(cls, pipeline: Pipeline) -> RunnableSequence:
        node_data = [
            Node(id=node.flow_id, label=node.label, type=node.type, params=node.params)
            for node in pipeline.node_set.all()
        ]
        edge_data = [Edge(**edge) for edge in pipeline.data["edges"]]
        return cls(nodes=node_data, edges=edge_data).build_runnable()

    def build_runnable(self) -> RunnableSequence:
        from apps.pipelines.nodes.base import PipelineState

        if not self.nodes:
            raise PipelineBuildError("There are no nodes in the graph")
        state_graph = StateGraph(PipelineState)

        self._add_nodes_to_graph(state_graph)
        self._add_edges_to_graph(state_graph)

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
        # compiled_graph.get_graph().print_ascii()
        return compiled_graph

    def _add_nodes_to_graph(self, state_graph):
        from apps.pipelines.nodes import nodes

        try:
            for node in self.nodes:
                node_class = getattr(nodes, node.type)
                node_instance = node_class(**node.params)
                incoming_edges = [edge.source for edge in self.edges if edge.target == node.id]
                state_graph.add_node(node.id, partial(node_instance.process, node.id, incoming_edges))
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

    def _add_edges_to_graph(self, state_graph):
        from apps.pipelines.nodes import nodes

        nodes_by_id = {node.id: node for node in self.nodes}
        seen_edges = set()
        conditional_edge_map = defaultdict(dict)
        for edge in self.edges:
            if edge.sourceHandle == "output_true":
                conditional_edge_map[edge.source][TRUE_NODE] = edge.target
            if edge.sourceHandle == "output_false":
                conditional_edge_map[edge.source][FALSE_NODE] = edge.target

        for edge in self.edges:
            if edge.is_conditional_edge() and edge.source not in seen_edges:
                node = nodes_by_id[edge.source]
                node_class = getattr(nodes, node.type)
                node_instance = node_class(**node.params)
                if not hasattr(node_instance, "_process_conditional"):
                    raise PipelineNodeBuildError("A conditional node needs a _process_conditional method")
                state_graph.add_conditional_edges(
                    edge.source, node_instance._process_conditional, path_map=conditional_edge_map[edge.source]
                )
            elif not edge.is_conditional_edge():
                state_graph.add_edge(edge.source, edge.target)
            seen_edges.add(edge.source)
