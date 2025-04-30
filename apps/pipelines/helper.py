from uuid import uuid4


def duplicate_pipeline_with_new_ids(pipeline_data):
    old_to_new_node_ids = {}
    new_nodes = []
    new_edges = []

    for node in pipeline_data.get("nodes", []):
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
        new_node = node.copy()
        new_node["id"] = new_id
        new_node["data"] = new_node["data"].copy()
        new_node["data"]["id"] = new_id

        if "params" in new_node["data"]:
            new_node["data"]["params"] = new_node["data"]["params"].copy()
            new_node["data"]["params"]["name"] = new_id

        new_nodes.append(new_node)

    for edge in pipeline_data.get("edges", []):
        old_source_id = edge["source"]
        old_target_id = edge["target"]

        new_source_id = old_to_new_node_ids.get(old_source_id, old_source_id)
        new_target_id = old_to_new_node_ids.get(old_target_id, old_target_id)

        target_node_data_type = None
        for node in new_nodes:
            if node["id"] == new_target_id and "data" in node and "type" in node["data"]:
                target_node_data_type = node["data"]["type"]
                break

        edge_id_parts = [new_source_id]
        if target_node_data_type and target_node_data_type not in ["StartNode", "EndNode"]:
            edge_id_parts.append(target_node_data_type)

        edge_id_parts.append(new_target_id)
        new_edge_id = f"reactflow__edge-{'-'.join(edge_id_parts)}-{uuid4().hex[:8]}"

        new_edge = edge.copy()
        new_edge["id"] = new_edge_id
        new_edge["source"] = new_source_id
        new_edge["target"] = new_target_id
        if "sourceHandle" in edge:
            new_edge["sourceHandle"] = edge["sourceHandle"]
        if "targetHandle" in edge:
            new_edge["targetHandle"] = edge["targetHandle"]

        new_edges.append(new_edge)

    new_pipeline_data = {
        "nodes": new_nodes,
        "edges": new_edges,
        "errors": pipeline_data.get("errors", {}),
    }

    return new_pipeline_data
