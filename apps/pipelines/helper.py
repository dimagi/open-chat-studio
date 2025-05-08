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
            if new_node["data"]["params"].get("name") == old_id:
                new_node["data"]["params"]["name"] = new_id

        new_nodes.append(new_node)

    for edge in pipeline_data.get("edges", []):
        old_source_id = edge["source"]
        old_target_id = edge["target"]

        new_source_id = old_to_new_node_ids.get(old_source_id, old_source_id)
        new_target_id = old_to_new_node_ids.get(old_target_id, old_target_id)

        new_edge = edge.copy()
        new_edge["source"] = new_source_id
        new_edge["target"] = new_target_id

        new_edges.append(new_edge)

    new_pipeline_data = {
        "nodes": new_nodes,
        "edges": new_edges,
        "errors": pipeline_data.get("errors", {}),
    }

    return new_pipeline_data
