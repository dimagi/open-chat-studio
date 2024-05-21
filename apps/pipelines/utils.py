from langchain_core.runnables import (
    RunnableSequence,
)
from langgraph.graph import StateGraph

from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import PipelineState


def build_runnable(pipeline: Pipeline) -> RunnableSequence:
    graph = PipelineGraph.from_json(pipeline.data)
    return build_runnable_from_graph(graph)


def build_runnable_from_graph(graph: PipelineGraph) -> RunnableSequence:
    from apps.pipelines.nodes import nodes

    if not graph.nodes:
        raise PipelineBuildError("There are no nodes in the graph")
    state_graph = StateGraph(PipelineState)
    for node in graph.nodes:
        node_class = getattr(nodes, node.type)
        state_graph.add_node(node.id, node_class.get_callable(node))
    for edge in graph.edges:
        state_graph.add_edge(edge.source, edge.target)
    state_graph.set_entry_point(graph.sorted_nodes[0].id)
    state_graph.set_finish_point(graph.sorted_nodes[-1].id)
    compiled_graph = state_graph.compile()
    return compiled_graph

    # first_node = graph.sorted_nodes[0]
    # first_node_class = getattr(nodes, first_node.type)
    # if len(graph.nodes) == 1:
    #     # It isn't considered a "chain" if there is only a single runnable
    #     runnable = RunnablePassthrough() | first_node_class.build(first_node)
    # else:
    #     runnable = first_node_class.build(first_node)
    #     for node in graph.sorted_nodes[1:]:
    #         node_class = getattr(nodes, node.type)
    #         runnable |= node_class.build(node)
    # return runnable
