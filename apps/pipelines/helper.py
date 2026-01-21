import copy
from uuid import uuid4

from apps.pipelines.flow import FlowNode, FlowNodeData


def duplicate_pipeline_with_new_ids(pipeline_data):
    new_data = copy.deepcopy(pipeline_data)
    old_to_new_node_ids = {}
    for node in new_data.get("nodes", []):
        old_id = node["id"]
        node_type = node.get("type")
        data_type = node.get("data", {}).get("type")

        if node_type == "startNode" or node_type == "endNode":
            new_id = str(uuid4())
        elif data_type:
            new_id = f"{data_type}-{uuid4().hex[:5]}"
        else:
            new_id = str(uuid4())

        old_to_new_node_ids[old_id] = new_id
        node["id"] = new_id
        node["data"]["id"] = new_id

        if "params" in node["data"] and node["data"]["params"].get("name") == old_id:
            node["data"]["params"]["name"] = new_id

    for edge in new_data.get("edges", []):
        old_source_id = edge["source"]
        old_target_id = edge["target"]

        new_source_id = old_to_new_node_ids.get(old_source_id, old_source_id)
        new_target_id = old_to_new_node_ids.get(old_target_id, old_target_id)

        edge["source"] = new_source_id
        edge["target"] = new_target_id

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
    from apps.pipelines.models import Pipeline

    pipeline = Pipeline.objects.create(
        team=team, name=name, data={"nodes": [node.model_dump() for node in all_flow_nodes], "edges": edges}
    )
    pipeline.update_nodes_from_data()
    return pipeline


def _get_start_and_end_nodes(start_x=100, end_x=800):
    from apps.pipelines.nodes.nodes import EndNode, StartNode

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
