import copy
from uuid import uuid4

from apps.pipelines.flow import FlowNode, FlowNodeData


def duplicate_pipeline_with_new_ids(pipeline_data, node_types: dict[str, str]):
    """Generate fresh node ids and rewrite the edges of a layout-only graph.

    ``Pipeline.data`` no longer lists nodes (ADR-0048), so membership and the id-to-type
    mapping come from ``node_types`` (flow_id -> ``Node.type``, built from the rows). Reserved
    start/end nodes get an opaque uuid; other nodes keep human-readable ids
    (``LLMResponseWithPrompt-a1b2c``). The node content itself lives on the rows and is
    renamed by ``Node.create_new_version``; here we only produce the id mapping and apply it
    to the edges.

    Any residual ``nodes`` key (an un-migrated old-format source) is dropped so the copy is
    layout-only: keeping it would leave a stale, un-remapped blob that the strip migration
    can no longer heal, since the copied rows now use new ids.
    """
    new_data = copy.deepcopy(pipeline_data)
    new_data.pop("nodes", None)
    old_to_new_node_ids = {}
    for old_id, node_type in node_types.items():
        if node_type in ("StartNode", "EndNode"):
            new_id = str(uuid4())
        else:
            new_id = f"{node_type}-{uuid4().hex[:5]}"
        old_to_new_node_ids[old_id] = new_id

    for edge in new_data.get("edges", []):
        edge["source"] = old_to_new_node_ids.get(edge["source"], edge["source"])
        edge["target"] = old_to_new_node_ids.get(edge["target"], edge["target"])

    return new_data, old_to_new_node_ids


def create_pipeline_with_nodes(team, name, middle_node=None):
    """
    Create a pipeline with start -> middle node -> end structure.
    """
    end_node, start_node = _get_start_and_end_nodes()
    all_flow_nodes = [start_node]
    if middle_node:
        all_flow_nodes.append(middle_node)
    all_flow_nodes.append(end_node)
    edges = []
    if middle_node:
        for i in range(len(all_flow_nodes) - 1):
            current_node = all_flow_nodes[i]
            next_node = all_flow_nodes[i + 1]
            edge = {
                "id": f"edge-{current_node.id}-{next_node.id}",
                "source": current_node.id,
                "target": next_node.id,
                "sourceHandle": "output",
                "targetHandle": "input",
            }
            edges.append(edge)
    return _create_pipeline(team, name, all_flow_nodes, edges)


def _create_pipeline(team, name, all_flow_nodes, edges):
    from apps.pipelines.models import Pipeline  # noqa: PLC0415 - circular: models.py imports helper at module level

    # Stored data is layout-only (ADR-0048): the nodes go to their own rows.
    pipeline = Pipeline.objects.create(team=team, name=name, data={"edges": edges})
    pipeline.update_nodes_from_data({node.id: node for node in all_flow_nodes})
    return pipeline


def _get_start_and_end_nodes(start_x=100, end_x=800):
    from apps.pipelines.nodes.nodes import (  # noqa: PLC0415 - circular: nodes.nodes→models→helper→nodes.nodes
        EndNode,
        StartNode,
    )

    start_node_id = str(uuid4())
    end_node_id = str(uuid4())
    start_node = FlowNode(
        id=start_node_id,
        type="startNode",
        position={"x": start_x, "y": 200},
        data=FlowNodeData(
            id=start_node_id,
            type=StartNode.__name__,
            label="",
            params={"name": "start"},
        ),
    )
    end_node = FlowNode(
        id=end_node_id,
        type="endNode",
        position={"x": end_x, "y": 200},
        data=FlowNodeData(
            id=end_node_id,
            type=EndNode.__name__,
            label="",
            params={"name": "end"},
        ),
    )
    return end_node, start_node
