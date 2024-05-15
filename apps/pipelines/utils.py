from langchain_core.runnables import (
    Runnable,
    RunnablePassthrough,
)

from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline


def build_runnable(pipeline: Pipeline) -> Runnable:
    graph = PipelineGraph.from_json(pipeline.data)
    return build_runnable_from_graph(graph)


def build_runnable_from_graph(graph: PipelineGraph) -> Runnable:
    from apps.pipelines.nodes import nodes

    if not graph.nodes:
        raise PipelineBuildError("There are no nodes in the graph")

    first_node = graph.sorted_nodes[0]
    first_node_class = getattr(nodes, first_node.type)
    if len(graph.nodes) == 1:
        # It isn't considered a "chain" if there is only a single runnable
        runnable = RunnablePassthrough() | first_node_class.build(first_node)
    else:
        runnable = first_node_class.build(first_node)
        for node in graph.sorted_nodes[1:]:
            node_class = getattr(nodes, node.type)
            runnable |= node_class.build(node)
    return runnable
