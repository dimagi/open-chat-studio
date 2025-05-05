from collections import Counter, defaultdict
from functools import cached_property, partial
from typing import Self

import pydantic
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field
from pydantic_core import ValidationError

from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import PipelineRouterNode
from apps.pipelines.nodes.nodes import EndNode, StartNode


class Node(pydantic.BaseModel):
    id: str
    label: str
    type: str
    params: dict = {}

    @property
    def pipeline_node_class(self):
        from apps.pipelines.nodes import nodes

        return getattr(nodes, self.type)

    @property
    def pipeline_node_instance(self):
        return self.pipeline_node_class(**self.params)


class Edge(pydantic.BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = STANDARD_OUTPUT_NAME

    def is_conditional(self):
        return self.sourceHandle != STANDARD_OUTPUT_NAME


class PipelineGraph(pydantic.BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    lenient_validation: bool = Field(default=False, description="Skip some validation checks. Used in tests.")

    @cached_property
    def nodes_by_id(self) -> dict[str, Node]:
        return {node.id: node for node in self.nodes}

    @cached_property
    def edges_by_source(self) -> dict[str, list[Edge]]:
        by_source = defaultdict(list)
        for edge in self.edges:
            by_source[edge.source].append(edge)
        return by_source

    @cached_property
    def conditional_edges(self) -> list[Edge]:
        return [edge for edge in self.edges if edge.is_conditional()]

    @cached_property
    def start_node(self) -> Node:
        start_nodes = [node for node in self.nodes if node.type == StartNode.__name__]
        return start_nodes[0]

    @cached_property
    def end_node(self) -> Node:
        end_nodes = [node for node in self.nodes if node.type == EndNode.__name__]
        return end_nodes[0]

    @cached_property
    def conditional_edge_map(self) -> dict[str, dict[str, str]]:
        conditional_edge_map = defaultdict(dict)
        for edge in self.conditional_edges:
            source_node = self.nodes_by_id[edge.source].pipeline_node_instance
            output_map = source_node.get_output_map()
            # this creates a map of the form:
            # {source_node: {'source_handle_1': value_to_follow_edge_1, 'source_handle_2': value_to_follow_edge_2}}
            conditional_edge_map[edge.source][output_map[edge.sourceHandle]] = edge.target
        return conditional_edge_map

    @cached_property
    def unconditional_edges(self) -> list[Edge]:
        return [edge for edge in self.edges if not edge.is_conditional()]

    @classmethod
    def build_runnable_from_pipeline(cls, pipeline: Pipeline) -> CompiledStateGraph:
        return cls.build_from_pipeline(pipeline).build_runnable()

    @classmethod
    def build_from_pipeline(cls, pipeline: Pipeline) -> Self:
        node_data = [
            Node(id=node.flow_id, label=node.label, type=node.type, params=node.params)
            for node in pipeline.node_set.all()
        ]
        edge_data = [Edge(**edge) for edge in pipeline.data["edges"]]
        return cls(nodes=node_data, edges=edge_data)

    def build_runnable(self) -> CompiledStateGraph:
        from apps.pipelines.nodes.base import PipelineState

        if not self.nodes:
            raise PipelineBuildError("There are no nodes in the graph")

        self._validate_start_end_nodes()
        if not self.lenient_validation:
            self._validate_no_parallel_nodes()
        if self._check_for_cycles():
            raise PipelineBuildError("A cycle was detected")

        state_graph = StateGraph(PipelineState)

        state_graph.set_entry_point(self.start_node.id)
        state_graph.set_finish_point(self.end_node.id)

        reachable_nodes = self._get_reachable_nodes(self.start_node)
        self._add_nodes_to_graph(state_graph, reachable_nodes)
        self._add_edges_to_graph(state_graph, reachable_nodes)

        try:
            compiled_graph = state_graph.compile()
        except ValueError as e:
            raise PipelineBuildError(str(e)) from e
        return compiled_graph

    def _validate_no_parallel_nodes(self):
        """This is a simple check to ensure that no two edges are connected to the same output
        which serves as a proxy for parallel nodes."""
        outgoing_edges = defaultdict(list)
        for edge in self.edges:
            outgoing_edges[edge.source].append(edge)

        for source, edges in outgoing_edges.items():
            handles = Counter(edge.sourceHandle for edge in edges)
            handle, count = handles.most_common(1)[0]
            if count > 1:
                edge_ids = [edge.id for edge in edges if edge.sourceHandle == handle]
                raise PipelineBuildError(
                    "Multiple edges connected to the same output", node_id=source, edge_ids=edge_ids
                )

    def _check_for_cycles(self):
        """Detect cycles in a directed graph."""
        adjacency_list = defaultdict(list)
        for edge in self.edges:
            adjacency_list[edge.source].append(edge.target)
        adjacency_list = dict(adjacency_list)

        state = {node.id: "unvisited" for node in self.nodes}

        def dfs(node_id: str) -> bool:
            if state[node_id] == "visiting":
                return True  # Found a cycle
            if state[node_id] == "visited":
                return False  # Already processed

            state[node_id] = "visiting"
            for neighbor in adjacency_list.get(node_id, []):
                if dfs(neighbor):
                    return True
            state[node_id] = "visited"
            return False

        for node_id in adjacency_list:
            if state[node_id] == "unvisited":
                if dfs(node_id):
                    return True

        return False

    def _get_reachable_nodes(self, start_node: Node) -> list[Node]:
        visited = set()
        stack = [start_node.id]
        while stack:
            node_id = stack.pop()
            visited.add(node_id)
            stack.extend([edge.target for edge in self.edges_by_source[node_id]])
        return list(self.nodes_by_id[node_id] for node_id in visited)

    def _add_nodes_to_graph(self, state_graph: StateGraph, nodes: list[Node]):
        if self.end_node not in nodes:
            raise PipelineBuildError(
                f"{EndNode.model_config['json_schema_extra'].label} node is not reachable "
                f"from {StartNode.model_config['json_schema_extra'].label} node",
                node_id=self.end_node.id,
            )

        for node in nodes:
            try:
                node_instance = node.pipeline_node_instance
                incoming_edges = [edge.source for edge in self.edges if edge.target == node.id]
                if isinstance(node_instance, PipelineRouterNode):
                    edge_map = self.conditional_edge_map[node.id]
                    router_function = node_instance.build_router_function(node.id, edge_map, incoming_edges)
                    state_graph.add_node(node.id, router_function)
                else:
                    outgoing_edges = [edge.target for edge in self.edges if edge.source == node.id]
                    state_graph.add_node(
                        node.id, partial(node_instance.process, node.id, incoming_edges, outgoing_edges)
                    )
            except ValidationError as ex:
                raise PipelineNodeBuildError(ex) from ex

    def _add_edges_to_graph(self, state_graph: StateGraph, reachable_nodes: list[Node]):
        for node in reachable_nodes:
            for edge in self.edges_by_source[node.id]:
                if not edge.is_conditional():
                    # conditional edges are handled by router node outputs
                    state_graph.add_edge(edge.source, edge.target)

    def _validate_start_end_nodes(self):
        start_nodes = [node for node in self.nodes if node.type == StartNode.__name__]
        if len(start_nodes) != 1:
            raise PipelineBuildError(
                f"There should be exactly 1 {StartNode.model_config['json_schema_extra'].label} node"
            )
        end_nodes = [node for node in self.nodes if node.type == EndNode.__name__]
        if len(end_nodes) != 1:
            raise PipelineBuildError(
                f"There should be exactly 1 {EndNode.model_config['json_schema_extra'].label} node"
            )
