from langchain_core.runnables import (
    Runnable,
    RunnablePassthrough,
)

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline


def build_runnable(pipeline: Pipeline, session_id: str | None = None) -> Runnable:
    graph = PipelineGraph.from_json(pipeline.data)
    return build_runnable_from_graph(graph)


def build_runnable_from_graph(graph: PipelineGraph) -> Runnable:
    from apps.pipelines import nodes

    runnable = RunnablePassthrough()
    for node in graph.nodes:
        node_class = getattr(nodes, node.type)
        runnable |= node_class.build(node)
    return runnable
