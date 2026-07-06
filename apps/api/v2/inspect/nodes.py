from django.db.models import Prefetch

from apps.documents.models import Collection
from apps.pipelines.models import Node


def inspect_node_queryset():
    """A Node queryset with every resource relation the inspect serializers render preloaded.

    Used for the chatbot's own pipeline and any embedded pipeline_start pipeline, so a node's
    FK/M2M relations resolve without per-node queries.
    """
    return Node.objects.select_related(
        "llm_provider",
        "llm_provider_model",
        "source_material",
        "assistant",
        "synthetic_voice",
        "synthetic_voice__voice_provider",
        "collection",
        "collection__llm_provider",
        "collection__embedding_provider_model",
    ).prefetch_related(
        "collection__files",
        Prefetch(
            "collection_indexes",
            queryset=Collection.objects.select_related("llm_provider", "embedding_provider_model").prefetch_related(
                "files"
            ),
        ),
        "custom_action_operations__custom_action__auth_provider",
    )


def node_render_order(node) -> int:
    """Sort key that puts the start node first and the end node last, leaving the rest in order."""
    return {"StartNode": 0, "EndNode": 2}.get(node.type, 1)


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Build a lightweight view of the pipeline's shape.

    Returns just the nodes (each as ``{flow_id, type, label}``) and the edges between them, with
    canvas positions removed and the edge handle keys renamed to snake_case.
    """
    nodes = [{"flow_id": node.flow_id, "type": node.type, "label": node.label} for node in node_list]
    edges = [
        {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("sourceHandle"),
            "target_handle": edge.get("targetHandle"),
        }
        for edge in (pipeline_data or {}).get("edges", [])
    ]
    return {"nodes": nodes, "edges": edges}
