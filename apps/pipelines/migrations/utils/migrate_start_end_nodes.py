from uuid import uuid4

import logging
from apps.pipelines.flow import FlowEdge, FlowNode, FlowNodeData
from apps.pipelines.graph import PipelineGraph

from apps.pipelines.nodes.nodes import EndNode, StartNode

logger = logging.getLogger(__name__)


def remove_all_start_end_nodes(Node):
    for start_node in Node.objects.filter(type=StartNode.__name__).all():
        pipeline = start_node.pipeline
        edges = pipeline.data["edges"]
        pipeline.data["edges"] = [edge for edge in edges if edge["source"] != start_node.flow_id]
        _drop_node(pipeline, start_node, Node)

    for end_node in Node.objects.filter(type=EndNode.__name__).all():
        pipeline = end_node.pipeline
        edges = pipeline.data["edges"]
        pipeline.data["edges"] = [edge for edge in edges if edge["target"] != end_node.flow_id]
        _drop_node(pipeline, end_node, Node)


def _drop_node(pipeline, node, Node):
    """Take ``node`` out of ``pipeline`` and persist the data.

    This sweep runs over every pipeline in the DB, so it meets both data shapes. One that
    still embeds a nodes list loses the entry and has its rows re-synced from the list. One
    whose list is gone (ADR-0049) has the row deleted directly: the rows own membership
    there, and handing ``_set_new_nodes`` a missing list would read as "no nodes" and delete
    every row on the pipeline.
    """
    if "nodes" in pipeline.data:
        pipeline.data["nodes"] = [n for n in pipeline.data["nodes"] if n["id"] != node.flow_id]
        _set_new_nodes(pipeline, Node)
    else:
        Node.objects.filter(pipeline=pipeline, flow_id=node.flow_id).delete()
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
    new_nodes = []
    new_edges = []

    try:
        current_start_id = list(node_ids - outgoing)[0]
        current_end_id = list(node_ids - incoming)[0]
        current_start_node = next(node for node in data["nodes"] if node["id"] == current_start_id)
        current_end_node = next(node for node in data["nodes"] if node["id"] == current_end_id)
    except (IndexError, StopIteration):
        new_nodes, new_edges = _get_default_nodes()  # Just add start and end nodes as the pipeline is in a bad state anyway...
        logger.exception("A pipeline is in a bad state, either it is recursive or pipeline.data['nodes'] doesn't match pipeline.node_set. Pipeline id: %s, team: %s", pipeline.id, pipeline.team)
    else:
        if not has_start:
            start_id = str(uuid4())
            new_start_node = FlowNode(
                id=start_id,
                type="startNode",
                position=_get_new_position(current_start_node, -200),
                data=FlowNodeData(id=start_id, type=StartNode.__name__),
            )
            new_start_edge = FlowEdge(
                id=str(uuid4()),
                source=new_start_node.id,
                target=current_start_id,
                sourceHandle="output",
                targetHandle="input",
            )
            new_nodes.append(new_start_node.model_dump())
            new_edges.append(new_start_edge.model_dump())

        if not has_end:
            end_id = str(uuid4())
            new_end_node = FlowNode(
                id=end_id,
                type="endNode",
                position=_get_new_position(current_end_node, 350),
                data=FlowNodeData(id=end_id, type=EndNode.__name__),
            )
            new_end_edge = FlowEdge(
                id=str(uuid4()),
                source=current_end_id,
                target=new_end_node.id,
                sourceHandle="output",
                targetHandle="input",
            )
            new_nodes.append(new_end_node.model_dump())
            new_edges.append(new_end_edge.model_dump())

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
        if node.data is None:
            # Layout-only node (ADR-0046): the Node row owns the content, so there is nothing to sync.
            continue
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


def _get_default_nodes():
    start_id= str(uuid4())
    start_node = FlowNode(
        id=start_id,
        type="startNode",
        position={
            "x": -200,
            "y": 200,
        },
        data=FlowNodeData(id=start_id, type=StartNode.__name__),
    )
    end_id= str(uuid4())
    end_node = FlowNode(
        id=end_id,
        type="endNode",
        position={"x": 1000, "y": 200},
        data=FlowNodeData(id=end_id, type=EndNode.__name__),
    )
    new_edge = FlowEdge(
        id=str(uuid4()),
        source=start_node.id,
        target=end_node.id,
        sourceHandle="output",
        targetHandle="input",
    )
    return [start_node.model_dump(), end_node.model_dump()], [new_edge.model_dump()]


def _create_default_nodes(pipeline, Node):
    default_nodes, default_edges = _get_default_nodes()
    pipeline.data = {"nodes": default_nodes, "edges": default_edges}
    _set_new_nodes(pipeline, Node)
    pipeline.save()
