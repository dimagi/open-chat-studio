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


def nodes_in_render_order(pipeline) -> list:
    """The pipeline's nodes in a stable order: start node first, end node last, the rest by id.

    ``Node`` declares no default ordering, so ``node_set.all()`` comes back in whatever order the
    database happens to yield and the same pipeline can serialise its nodes differently between
    requests. Sorting happens in Python rather than via ``order_by`` so it reads the prefetched rows
    (see ``inspect_node_queryset``) instead of costing another query.
    """
    return sorted(pipeline.node_set.all(), key=lambda node: (node_render_order(node), node.id))


def graph_digest(node_list, pipeline_data: dict | None) -> dict:
    """Build a lightweight view of the pipeline's shape.

    Returns just the nodes (each as ``{flow_id, type, label}``) and the edges between them — each
    with its ``id`` — with canvas positions removed and the edge handle keys renamed to snake_case.
    """
    nodes = [{"flow_id": node.flow_id, "type": node.type, "label": node.label} for node in node_list]
    edges = [
        {
            "id": edge.get("id"),
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("sourceHandle"),
            "target_handle": edge.get("targetHandle"),
        }
        for edge in (pipeline_data or {}).get("edges", [])
    ]
    return {"nodes": nodes, "edges": edges}
