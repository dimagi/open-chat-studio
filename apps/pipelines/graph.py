from collections import defaultdict
from functools import cached_property

import pydantic
from toposort import toposort_flatten

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

    @cached_property
    def sorted_nodes(self):
        """Assume a DAG"""
        if len(self.nodes) == 1 and not self.edges:
            # In the case that there are no edges, the nodes are already sorted...
            return self.nodes

        dependency_graph = defaultdict(set)
        for edge in self.edges:
            dependency_graph[edge.target].add(edge.source)

        toposorted_ids = toposort_flatten(dependency_graph)

        for node in self.nodes:
            if node.id not in toposorted_ids:
                raise PipelineBuildError(f"Node: {node.id} is orphaned and has no edges attached to it")

        node_ids_to_nodes = {node.id: node for node in self.nodes}
        sorted_nodes = [node_ids_to_nodes[node_id] for node_id in toposorted_ids]

        return sorted_nodes
