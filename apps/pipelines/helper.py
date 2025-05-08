import copy
from uuid import uuid4


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
