from collections import defaultdict
from functools import cached_property, partial
from typing import Any, Self

import pydantic
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic_core import ValidationError

from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes import nodes as pipeline_nodes_module
from apps.pipelines.nodes.base import PipelineRouterNode, PipelineState
from apps.pipelines.nodes.nodes import CodeNode, EndNode, StartNode
from apps.service_providers.llm_service.retry import get_retry_policy


class Node(pydantic.BaseModel):
    id: str
    label: str
    type: str
    params: dict = {}
    django_node: Any = None

    @property
    def pipeline_node_class(self):
        return getattr(pipeline_nodes_module, self.type)

    @cached_property
    def pipeline_node_instance(self):
        return self.pipeline_node_class(node_id=self.id, django_node=self.django_node, **self.params)

    @property
    def name(self):
        if self.type == StartNode.__name__:
            return "start"
        if self.type == EndNode.__name__:
            return "end"
        return self.params.get("name") or self.id


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

    @property
    def node_id_to_name_mapping(self):
        return {node.id: node.name for node in self.nodes}

    @property
    def filter_patterns(self):
        """Run names to exclude from tracing"""
        return [
            self.start_node.id,
            self.end_node.id,
        ]

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
    def reachable_nodes(self) -> list[Node]:
        """The nodes the build actually wires. Requires exactly one Start node and no cycle."""
        return self._get_reachable_nodes(self.start_node)

    @cached_property
    def reachable_ids(self) -> set[str]:
        return {node.id for node in self.reachable_nodes}

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
            Node(id=node.flow_id, label=node.label, type=node.type, params=node.params, django_node=node)
            for node in pipeline.node_set.all()
        ]
        edge_data = [Edge(**edge) for edge in pipeline.data["edges"]]
        return cls(nodes=node_data, edges=edge_data)

    @cached_property
    def build_errors(self) -> list[PipelineBuildError]:
        """Every structural problem with this graph that is checkable, not just the first.

        The checks tier, because later ones presuppose earlier ones: reachability needs exactly one
        Start node. So a failing tier suppresses the tiers below it — those answers don't exist yet,
        rather than being withheld for brevity.

        Node params are *not* checked here; ``Pipeline._node_validation_errors`` owns that and merges
        its results with these. This avoids building node instances wherever it can, so an invalid
        node doesn't stop it reporting what it can.
        """
        if not self.nodes:
            return [PipelineBuildError("There are no nodes in the graph")]

        # Tier 1 — needs nothing.
        errors = self._start_end_node_errors()
        if self._check_for_cycles():
            errors.append(PipelineBuildError("A cycle was detected"))

        # Tier 2 — needs exactly one Start/End node.
        if errors:
            return errors
        if self.end_node not in self.reachable_nodes:
            errors.append(
                PipelineBuildError(
                    f"{EndNode.model_config['json_schema_extra'].label} node is not reachable "
                    f"from {StartNode.model_config['json_schema_extra'].label} node",
                    node_id=self.end_node.id,
                )
            )
        return errors

    def build_runnable(self) -> CompiledStateGraph:
        # build_errors is the single source of truth for what's wrong with the graph; the runtime
        # still fails fast, on the first problem in dependency order. Cached, so a caller that has
        # already read it (Pipeline.validate) doesn't pay for it twice.
        if errors := self.build_errors:
            raise errors[0]

        state_graph = StateGraph(PipelineState)

        state_graph.set_entry_point(self.start_node.id)
        state_graph.set_finish_point(self.end_node.id)

        self._add_nodes_to_graph(state_graph, self.reachable_nodes)
        self._add_edges_to_graph(state_graph, self.reachable_nodes)

        try:
            compiled_graph = state_graph.compile()
        except ValueError as e:
            raise PipelineBuildError(str(e)) from e
        return compiled_graph  # ty: ignore[invalid-return-type]

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
        # End-reachability is checked by build_errors, which build_runnable reads first.
        retry_policy = get_retry_policy()

        for node in nodes:
            try:
                node_instance = node.pipeline_node_instance
                incoming_nodes = [edge.source for edge in self.edges if edge.target == node.id]
                if isinstance(node_instance, PipelineRouterNode):
                    edge_map = self.conditional_edge_map[node.id]
                    router_function = node_instance.build_router_function(edge_map, incoming_nodes)
                    state_graph.add_node(node.id, router_function, retry_policy=retry_policy)
                else:
                    outgoing_nodes = [edge.target for edge in self.edges if edge.source == node.id]
                    state_graph.add_node(
                        node.id,
                        partial(node_instance.process, incoming_nodes, outgoing_nodes),
                        retry_policy=retry_policy,
                    )
            except ValidationError as ex:
                raise PipelineNodeBuildError(ex) from ex

    def _add_edges_to_graph(self, state_graph: StateGraph, reachable_nodes: list[Node]):
        for node in reachable_nodes:
            if node.type == CodeNode.__name__:
                # CodeNode manages its own routing similar to conditional nodes
                continue
            for edge in self.edges_by_source[node.id]:
                if not edge.is_conditional():
                    # conditional edges are handled by router node outputs
                    state_graph.add_edge(edge.source, edge.target)

    def _start_end_node_errors(self) -> list[PipelineBuildError]:
        """Both counts, reported together — one missing terminal shouldn't hide the other."""
        errors = []
        for node_class in (StartNode, EndNode):
            matching = [node for node in self.nodes if node.type == node_class.__name__]
            if len(matching) != 1:
                label = node_class.model_config["json_schema_extra"].label
                errors.append(PipelineBuildError(f"There should be exactly 1 {label} node"))
        return errors
