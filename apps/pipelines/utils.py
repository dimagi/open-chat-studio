import importlib

from langchain_core.runnables import (
    Runnable,
    RunnablePassthrough,
)

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.nodes import ExperimentSessionId, PipelineNode


def build_runnable_from_graph(graph: PipelineGraph, session_id: str | None = None) -> Runnable:
    all_nodes = importlib.import_module("apps.pipelines.nodes")
    runnable = RunnablePassthrough()
    for node in graph.nodes:
        node_class = getattr(all_nodes, node.type)
        if _requires_session(node_class) and session_id is None:
            raise ValueError("The pipeline requires a session_id, but none was passed in")

        if _requires_session(node_class):
            new_runnable = node_class.build(node, session_id)
        else:
            new_runnable = node_class.build(node)
        runnable |= new_runnable

    return runnable


def _requires_session(node: PipelineNode):
    return any(field.type_ == ExperimentSessionId for field in node.__fields__.values())
