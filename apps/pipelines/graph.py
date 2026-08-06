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
from apps.pipelines.nodes.base import PipelineRouterNode, PipelineState, resolve_node_class
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
        """This node's class. Raises for a type that names no node class, which is what a removed
        type has always done — so a caller guarding against one guards against both."""
        node_class = resolve_node_class(self.type)
        if node_class is None:
            raise AttributeError(f"Unknown pipeline node type: {self.type}")
        return node_class

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
        # An explicit null handle means the default output — it is this field's own default, and
        # unwired_handles reads it that way too. Treating it as conditional would report the edge
        # stranded (no node offers a "None" handle) and drop it from the wired graph.
        return (self.sourceHandle or STANDARD_OUTPUT_NAME) != STANDARD_OUTPUT_NAME


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
    def _output_maps(self) -> dict[str, dict | None]:
        """Memo for :meth:`_output_map_for`, keyed by node id."""
        return {}

    def _output_map_for(self, node_id: str) -> dict | None:
        """A node's named output handles, or ``None`` when they can't be determined.

        Empty and unknown are different answers: a plain node genuinely offers no named handles (only
        the router types do), whereas a node whose params don't validate can't report its branches at
        all — and must not have its edges called stranded on the strength of that.

        Memoized because building the node instance runs its validators, which query the provider
        model for an LLM-backed router — and every conditional edge out of one asks the same question.

        Only the failures the node stage itself reports are treated as "unknown": a type naming no
        node class, and the two ways a validator declines its params. Anything else a validator
        raises — a ``DatabaseError`` above all, which would poison an enclosing
        ``transaction.atomic()`` if swallowed here — propagates.
        """
        if node_id not in self._output_maps:
            node = self.nodes_by_id[node_id]
            if resolve_node_class(node.type) is None:
                self._output_maps[node_id] = None  # unknown node type; the node stage names it
                return self._output_maps[node_id]
            try:
                node_instance = node.pipeline_node_instance
            except (ValidationError, PipelineNodeBuildError):
                self._output_maps[node_id] = None  # the node stage reports why this node is broken
            else:
                # ``get_output_map`` only exists on PipelineRouterNode.
                self._output_maps[node_id] = getattr(node_instance, "get_output_map", dict)()
        return self._output_maps[node_id]

    @cached_property
    def conditional_edge_map(self) -> dict[str, dict[str, str]]:
        """``{source_id: {branch_label: target_id}}`` for every resolvable reachable router edge.

        Edges stranded on a handle their source no longer offers are left out; reporting them is
        :meth:`_stranded_edge_errors`' job, which ``build_runnable`` runs before it builds anything.
        """
        conditional_edge_map = defaultdict(dict)
        # The build only wires reachable nodes, so this matches that scope: an unreachable router's
        # edges are the advisory unwired map's concern, not the build's.
        for edge in self.conditional_edges:
            if edge.source not in self.reachable_ids:
                continue
            output_map = self._output_map_for(edge.source) or {}
            if edge.sourceHandle in output_map:
                # {source_node: {'source_handle_1': value_to_follow_edge_1, ...}}
                conditional_edge_map[edge.source][output_map[edge.sourceHandle]] = edge.target
        return conditional_edge_map

    def _stranded_edge_errors(self) -> list[PipelineBuildError]:
        """Reachable conditional edges pointing at a handle their source doesn't offer.

        Nothing validates a ``sourceHandle`` against its source node on write, so an edge can be left
        pointing at a handle that no longer exists — a write that drops a router keyword but keeps the
        edge, or a named handle on a node that was never a router at all.
        """
        stranded = [
            edge.id
            for edge in self.conditional_edges
            if edge.source in self.reachable_ids
            and (output_map := self._output_map_for(edge.source)) is not None
            and edge.sourceHandle not in output_map
        ]
        if not stranded:
            return []
        return [
            PipelineBuildError("One or more edges reference a router output that no longer exists", edge_ids=stranded)
        ]

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
        Start node, and a dangling edge endpoint breaks both cycle detection and reachability. So a
        failing tier suppresses the tiers below it — those answers don't exist yet, rather than being
        withheld for brevity.

        Node params are *not* checked here; ``Pipeline._node_validation_errors`` owns that and merges
        its results with these. This avoids building node instances wherever it can, so an invalid
        node doesn't stop it reporting what it can — and it is cached, because the stranded-edge check
        does build the source node of a conditional edge, which costs queries.
        """
        if not self.nodes:
            return [PipelineBuildError("There are no nodes in the graph")]

        # Tier 1 — needs nothing.
        errors = self._start_end_node_errors()
        endpoint_errors = self._dangling_edge_endpoint_errors()
        errors.extend(endpoint_errors)
        if not endpoint_errors and self._check_for_cycles():
            # Cycle detection walks edges by id, so it can't run over a dangling endpoint.
            errors.append(PipelineBuildError("A cycle was detected"))

        # Tier 2 — needs exactly one Start/End node and edges that resolve.
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
        errors.extend(self._stranded_edge_errors())
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

    def _dangling_edge_endpoint_errors(self) -> list[PipelineBuildError]:
        """Edges whose source or target names no node in the graph.

        Nothing cross-checks an edge's endpoints on write, and a dangling one otherwise surfaces as a
        bare ``KeyError`` from cycle detection or reachability. The edge is what's broken.
        """
        dangling = [
            edge.id for edge in self.edges if edge.source not in self.nodes_by_id or edge.target not in self.nodes_by_id
        ]
        if not dangling:
            return []
        return [PipelineBuildError("One or more edges reference a node that no longer exists", edge_ids=dangling)]
