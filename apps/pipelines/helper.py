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


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def convert_non_pipeline_experiment_to_pipeline(experiment):
    if experiment.pipeline:
        raise ValueError(f"Experiment already has a pipeline attached: {experiment.id}")
    elif experiment.assistant:
        pipeline = create_assistant_pipeline(experiment)
    elif experiment.llm_provider:
        pipeline = create_llm_pipeline(experiment)
    else:
        raise ValueError(f"Unknown experiment type for experiment {experiment.id}")

    experiment.pipeline = pipeline
    experiment.assistant = None
    experiment.llm_provider = None
    experiment.llm_provider_model = None
    experiment.save()


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def create_pipeline_with_node(experiment, node_type, node_label, node_params):
    """Create a pipeline with start -> custom_node -> end structure."""
    from apps.pipelines.models import Pipeline

    pipeline_name = f"{experiment.name} Pipeline"
    node_id = str(uuid4())
    node = FlowNode(
        id=node_id,
        type="pipelineNode",
        position={"x": 400, "y": 200},
        data=FlowNodeData(
            id=node_id,
            type=node_type,
            label=node_label,
            params=node_params,
        ),
    )
    return create_pipeline_with_nodes(team=experiment.team, name=pipeline_name, middle_node=node)


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def create_llm_pipeline(experiment):
    from apps.pipelines.nodes.nodes import LLMResponseWithPrompt

    """Create a start -> LLMResponseWithPrompt -> end nodes pipeline for an LLM experiment."""
    llm_params = {
        "name": "llm",
        "llm_provider_id": experiment.llm_provider.id,
        "llm_provider_model_id": experiment.llm_provider_model.id,
        "llm_temperature": experiment.temperature,
        "history_type": "global",
        "history_name": None,
        "history_mode": "summarize",
        "user_max_token_limit": experiment.llm_provider_model.max_token_limit,
        "max_history_length": 10,
        "source_material_id": experiment.source_material.id if experiment.source_material else None,
        "prompt": experiment.prompt_text or "",
        "tools": list(experiment.tools) if experiment.tools else [],
        "custom_actions": [
            op.get_model_id(False) for op in experiment.custom_action_operations.select_related("custom_action").all()
        ],
        "built_in_tools": [],
        "tool_config": {},
    }

    return create_pipeline_with_node(
        experiment=experiment, node_type=LLMResponseWithPrompt.__name__, node_label="LLM", node_params=llm_params
    )


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def create_assistant_pipeline(experiment):
    from apps.pipelines.nodes.nodes import AssistantNode

    """Create a start -> AssistantNode -> end nodes pipeline for an assistant experiment."""
    assistant_params = {
        "name": "assistant",
        "assistant_id": str(experiment.assistant.id),
        "citations_enabled": experiment.citations_enabled,
        "input_formatter": experiment.input_formatter or "",
    }

    return create_pipeline_with_node(
        experiment=experiment,
        node_type=AssistantNode.__name__,
        node_label="OpenAI Assistant",
        node_params=assistant_params,
    )


def create_pipeline_with_nodes(team, name, middle_node=None):
    """
    Create a pipeline with start -> middle node -> end structure.
    """
    from apps.pipelines.models import Pipeline
    from apps.pipelines.nodes.nodes import EndNode, StartNode

    start_node_id = str(uuid4())
    end_node_id = str(uuid4())
    start_node = FlowNode(
        id=start_node_id,
        type="startNode",
        position={"x": 100, "y": 200},
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
        position={"x": 800, "y": 200},
        data=FlowNodeData(
            id=end_node_id,
            type=EndNode.__name__,
            label="",
            params={"name": "end"},
        ),
    )
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
    pipeline = Pipeline.objects.create(
        team=team, name=name, data={"nodes": [node.model_dump() for node in all_flow_nodes], "edges": edges}
    )
    pipeline.update_nodes_from_data()
    return pipeline
