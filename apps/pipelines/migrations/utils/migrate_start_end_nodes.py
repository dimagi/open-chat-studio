from typing import TYPE_CHECKING
from uuid import uuid4

from apps.pipelines.flow import FlowNode
from apps.pipelines.graph import PipelineGraph

from apps.pipelines.nodes.nodes import EndNode, StartNode

def remove_all_start_end_nodes(Node):
    for start_node in Node.objects.filter(type=StartNode.__name__).all():
        pipeline = start_node.pipeline
        edges = pipeline.data["edges"]
        nodes = pipeline.data["nodes"]
        pipeline.data["edges"] = [edge for edge in edges if edge["source"] != start_node.flow_id]
        pipeline.data["nodes"] = [node for node in nodes if node["id"] != start_node.flow_id]
        _set_new_nodes(pipeline, Node)
        pipeline.save()

    for end_node in Node.objects.filter(type=EndNode.__name__).all():
        pipeline = end_node.pipeline
        edges = pipeline.data["edges"]
        nodes = pipeline.data["nodes"]
        pipeline.data["edges"] = [edge for edge in edges if edge["target"] != end_node.flow_id]
        pipeline.data["nodes"] = [node for node in nodes if node["id"] != end_node.flow_id]
        _set_new_nodes(pipeline, Node)
        pipeline.save()


def add_missing_start_end_nodes(pipeline, Node):
    nodes = pipeline.node_set.all()
    data = pipeline.data
    has_start = False
    has_end = False
    if not nodes:
        return _create_default_nodes(pipeline, Node)

    for node in nodes:
        if node.type == StartNode.__name__:
            has_start = True
        if node.type == EndNode.__name__:
            has_end = True

    if has_start and has_end:
        return

    graph = PipelineGraph.build_from_pipeline(pipeline)

    node_ids = {n.id for n in graph.nodes}
    incoming = {e.source for e in graph.edges}
    outgoing = {e.target for e in graph.edges}
    current_start_id = list(node_ids - outgoing)[0]
    current_end_id = list(node_ids - incoming)[0]
    current_start_node = next(node for node in data["nodes"] if node["id"] == current_start_id)
    current_end_node = next(node for node in data["nodes"] if node["id"] == current_end_id)

    new_nodes = []
    new_edges = []

    if not has_start:
        new_start_node = {
            "id": str(uuid4()),
            "type": "pipelineNode",
            "position": _get_new_position(current_start_node, -200),
            "data": {"id": str(uuid4()), "type": StartNode.__name__},
        }

        new_start_edge = {
            "id": str(uuid4()),
            "source": new_start_node["id"],
            "target": current_start_id,
            "sourceHandle": "output",
            "targetHandle": "input",
        }
        new_nodes.append(new_start_node)
        new_edges.append(new_start_edge)

    if not has_end:
        new_end_node = {
            "id": str(uuid4()),
            "type": "pipelineNode",
            "position": _get_new_position(current_end_node, 350),
            "data": {"id": str(uuid4()), "type": EndNode.__name__},
        }
        new_end_edge = {
            "id": str(uuid4()),
            "source": current_end_id,
            "target": new_end_node["id"],
            "sourceHandle": "output",
            "targetHandle": "input",
        }
        new_nodes.append(new_end_node)
        new_edges.append(new_end_edge)

    if data.get("nodes"):
        data["nodes"].extend(new_nodes)
    else:
        data["nodes"] = new_nodes

    if data.get("edges"):
        data["edges"].extend(new_edges)
    else:
        data["edges"] = new_edges

    pipeline.data = data
    # Set new nodes inline as this is run inside a migration
    _set_new_nodes(pipeline, Node)
    pipeline.save()


def _set_new_nodes(pipeline, Node):
    nodes = [FlowNode(**node) for node in pipeline.data["nodes"]]
    current_ids = set(pipeline.node_set.order_by("created_at").values_list("flow_id", flat=True).all())
    new_ids = set(node.id for node in nodes)
    to_delete = current_ids - new_ids
    Node.objects.filter(pipeline=pipeline, flow_id__in=to_delete).delete()
    for node in nodes:
        Node.objects.update_or_create(
            pipeline=pipeline,
            flow_id=node.id,
            defaults={
                "type": node.data.type,
                "params": node.data.params,
                "label": node.data.label,
            },
        )


def _get_new_position(node: dict, x_offset: int):
    try:
        x = node["position"]["x"] + x_offset
    except KeyError:
        x = x_offset

    try:
        y = node["position"]["y"]
    except KeyError:
        y = 200

    return {"x": x, "y": y}


def _create_default_nodes(pipeline, Node):
    start_node = {
        "id": str(uuid4()),
        "type": "pipelineNode",
        "position": {
            "x": -200,
            "y": 200,
        },
        "data": {"id": str(uuid4()), "type": StartNode.__name__},
    }
    end_node = {
        "id": str(uuid4()),
        "type": "pipelineNode",
        "position": {"x": 1000, "y": 200},
        "data": {"id": str(uuid4()), "type": EndNode.__name__},
    }
    new_edge = {
        "id": str(uuid4()),
        "source": start_node["id"],
        "target": end_node["id"],
        "sourceHandle": "output",
        "targetHandle": "input",
    }
    pipeline.data = {"nodes": [start_node, end_node], "edges": [new_edge]}
    _set_new_nodes(pipeline, Node)
