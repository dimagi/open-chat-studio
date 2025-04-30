from uuid import uuid4


def duplicate_pipeline_with_new_ids(pipeline_data):
    """
    Duplicates a pipeline data structure, generating new unique IDs for all
    nodes and edges while preserving the structure and connections.

    Node IDs for StartNode and EndNode are full UUIDs.
    Node IDs for other types (like LLMResponseWithPrompt, RouterNode) follow
    the 'NodeType-ShortHex' pattern.
    Edge IDs include the target node's data.type if it's not Start/End.

    Args:
        pipeline_data (dict): The original pipeline data dictionary.

    Returns:
        dict: The new pipeline data dictionary with unique IDs.
    """
    old_to_new_node_ids = {}
    new_nodes = []
    new_edges = []

    # 1. Create new nodes with new IDs and build the ID mapping
    for node in pipeline_data.get("nodes", []):
        old_id = node["id"]
        node_type = node.get("type")  # Top-level type (e.g., 'startNode', 'endNode', 'pipelineNode')
        data_type = node.get("data", {}).get(
            "type"
        )  # Specific type from data (e.g., 'StartNode', 'EndNode', 'LLMResponseWithPrompt')

        # Generate new ID based on node type
        if node_type == "startNode" or node_type == "endNode":
            new_id = str(uuid4())  # Full UUID for Start and End nodes
        elif data_type:
            # Use the specific data type for the prefix
            new_id = f"{data_type}-{uuid4().hex[:5]}"  # NodeType-ShortHex pattern
        else:
            # Fallback to full UUID if data.type is missing (shouldn't happen in valid data)
            new_id = str(uuid4())

        old_to_new_node_ids[old_id] = new_id

        # Create a new node dictionary with the new ID
        new_node = node.copy()
        new_node["id"] = new_id
        # Also update the id within the data field
        new_node["data"] = new_node["data"].copy()
        new_node["data"]["id"] = new_id

        # Update the name parameter to match the new ID
        if "params" in new_node["data"]:
            new_node["data"]["params"] = new_node["data"]["params"].copy()  # Ensure params dict is copied
            new_node["data"]["params"]["name"] = new_id

        new_nodes.append(new_node)

    # 2. Create new edges with new IDs and updated source/target references
    for edge in pipeline_data.get("edges", []):
        old_source_id = edge["source"]
        old_target_id = edge["target"]

        # Get the new source and target IDs using the mapping
        # Use .get() with a default to handle potential edge cases
        new_source_id = old_to_new_node_ids.get(old_source_id, old_source_id)
        new_target_id = old_to_new_node_ids.get(old_target_id, old_target_id)

        # Find the target node in the new_nodes list to get its data.type
        target_node_data_type = None
        for node in new_nodes:
            if node["id"] == new_target_id and "data" in node and "type" in node["data"]:
                target_node_data_type = node["data"]["type"]
                break  # Found the target node

        # Generate a new unique ID for the edge
        edge_id_parts = [new_source_id]  # Start with the new source ID

        # Add the target node's data.type if it exists and is not StartNode or EndNode
        if target_node_data_type and target_node_data_type not in ["StartNode", "EndNode"]:
            edge_id_parts.append(target_node_data_type)

        edge_id_parts.append(new_target_id)  # Add the new target ID

        # Combine parts and add a random hex suffix for extra uniqueness
        # The format reactflow__edge-sourceID-targetType-targetID-randomHex
        # or reactflow__edge-sourceID-targetID-randomHex for Start/End targets
        new_edge_id = f"reactflow__edge-{'-'.join(edge_id_parts)}-{uuid4().hex[:8]}"

        # Create a new edge dictionary with the new ID and updated source/target
        new_edge = edge.copy()
        new_edge["id"] = new_edge_id
        new_edge["source"] = new_source_id
        new_edge["target"] = new_target_id
        # Preserve sourceHandle and targetHandle
        if "sourceHandle" in edge:
            new_edge["sourceHandle"] = edge["sourceHandle"]
        if "targetHandle" in edge:
            new_edge["targetHandle"] = edge["targetHandle"]

        new_edges.append(new_edge)

    # 3. Construct the new pipeline data dictionary
    new_pipeline_data = {
        "nodes": new_nodes,
        "edges": new_edges,
        "errors": pipeline_data.get("errors", {}),  # Keep errors as is
    }

    return new_pipeline_data
