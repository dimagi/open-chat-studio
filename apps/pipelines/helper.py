import copy
from uuid import uuid4

from field_audit.models import AuditAction

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
    has_children = experiment.child_links.exists()
    if experiment.pipeline:
        raise ValueError(f"Experiment already has a pipeline attached: {experiment.id}")
    elif experiment.assistant:
        pipeline = create_assistant_pipeline(experiment)
    elif experiment.llm_provider:
        if has_children:
            pipeline = create_router_pipeline(experiment)
        else:
            pipeline = create_llm_pipeline(experiment)
    else:
        raise ValueError(f"Unknown experiment type for experiment {experiment.id}")

    experiment.pipeline = pipeline
    experiment.assistant = None
    experiment.llm_provider = None
    experiment.llm_provider_model = None
    experiment.save()

    if has_children:
        # archive child experiments and routes
        experiment.children.update(is_archived=True, audit_action=AuditAction.AUDIT)
        experiment.child_links.update(is_archived=True)


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def create_pipeline_with_node(experiment, node_type, node_label, node_params):
    """Create a pipeline with start -> custom_node -> end structure."""
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

    # Create a start -> LLMResponseWithPrompt -> end nodes pipeline for an LLM experiment
    llm_params = _get_llm_params_for_experiment(experiment)
    return create_pipeline_with_node(
        experiment=experiment, node_type=LLMResponseWithPrompt.__name__, node_label="LLM", node_params=llm_params
    )


def _get_params_generic(experiment, name, configure_voice=False):
    if experiment.assistant:
        return _get_assistant_node_params(experiment, name)
    elif experiment.llm_provider:
        return _get_llm_params_for_experiment(experiment, name, configure_voice)
    else:
        raise ValueError(f"Unknown experiment type for experiment {experiment.id}")


def _get_node_type(experiment):
    if experiment.assistant:
        return "AssistantNode"
    elif experiment.llm_provider:
        return "LLMResponseWithPrompt"
    else:
        raise ValueError(f"Unknown experiment type for experiment {experiment.id}")


def _get_llm_params_for_experiment(experiment, name="llm", configure_voice=False):
    llm_params = {
        "name": name,
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
    if configure_voice and experiment.voice_provider_id and experiment.synthetic_voice_id:
        llm_params["synthetic_voice_id"] = experiment.synthetic_voice_id
    return llm_params


def create_router_pipeline(experiment):
    def _make_node(node_type, node_label, node_params, x=200, y=200):
        node_id = str(uuid4())
        return FlowNode(
            id=node_id,
            type="pipelineNode",
            position={"x": x, "y": y},
            data=FlowNodeData(
                id=node_id,
                type=node_type,
                label=node_label,
                params=node_params,
            ),
        )

    from apps.experiments.models import ExperimentRouteType
    from apps.pipelines.nodes.nodes import RouterNode

    has_voice = experiment.voice_provider_id
    use_child_bot_voice = experiment.use_processor_bot_voice
    configure_child_voice = has_voice and use_child_bot_voice

    children_links = experiment.child_links.select_related("child").all()
    child_links = [link for link in children_links if link.type == ExperimentRouteType.PROCESSOR]
    terminal_links = [link for link in children_links if link.type == ExperimentRouteType.TERMINAL]
    default_index = [i for i, link in enumerate(child_links) if link.is_default]
    router_node = _make_node(
        RouterNode.__name__,
        "Router",
        {
            "name": experiment.name,
            "llm_provider_id": experiment.llm_provider.id,
            "llm_provider_model_id": experiment.llm_provider_model.id,
            "llm_temperature": experiment.temperature,
            "history_type": "node",
            "history_name": None,
            "history_mode": "max_history_length",
            "user_max_token_limit": experiment.llm_provider_model.max_token_limit,
            "max_history_length": 20,
            "source_material_id": experiment.source_material.id if experiment.source_material else None,
            "prompt": experiment.prompt_text or "",
            "tag_output_message": True,
            "keywords": [child.keyword for child in child_links],
            "default_keyword_index": default_index[0] if default_index else 0,
        },
        x=300,
    )

    child_nodes = [
        _make_node(
            _get_node_type(child_link.child),
            "LLM",
            _get_params_generic(child_link.child, child_link.child.name, configure_child_voice),
            x=700,
            y=(-200 + 400 * i),
        )
        for i, child_link in enumerate(child_links)
    ]

    terminal_nodes = []
    if terminal_links:
        terminal_link = terminal_links[0]
        terminal_nodes = [
            _make_node(
                _get_node_type(terminal_link.child),
                "terminal",
                _get_params_generic(terminal_link.child, terminal_link.child.name),
                x=1100,
            )
        ]

    end_node, start_node = _get_start_and_end_nodes(end_x=1500)
    all_nodes = [start_node, end_node, router_node] + child_nodes + terminal_nodes

    def _make_edge(from_node, to_node, source_handle="output"):
        return {
            "id": f"edge-{from_node.id}-{to_node.id}",
            "source": from_node.id,
            "target": to_node.id,
            "sourceHandle": source_handle,
            "targetHandle": "input",
        }

    edges = [
        _make_edge(start_node, router_node),
    ]
    for i, child in enumerate(child_nodes):
        edges.append(_make_edge(router_node, child, source_handle=f"output_{i}"))

    if terminal_nodes:
        for child in child_nodes:
            edges.append(_make_edge(child, terminal_nodes[0]))
        edges.append(_make_edge(terminal_nodes[0], end_node))
    else:
        for child in child_nodes:
            edges.append(_make_edge(child, end_node))

    return _create_pipeline(experiment.team, experiment.name, all_nodes, edges)


# TODO: function is temporary and can be deleted after the exp -> chatbot transition is complete
def create_assistant_pipeline(experiment):
    from apps.pipelines.nodes.nodes import AssistantNode

    """Create a start -> AssistantNode -> end nodes pipeline for an assistant experiment."""
    assistant_params = _get_assistant_node_params(experiment)
    return create_pipeline_with_node(
        experiment=experiment,
        node_type=AssistantNode.__name__,
        node_label="OpenAI Assistant",
        node_params=assistant_params,
    )


def _get_assistant_node_params(experiment, name="assistant"):
    assistant_params = {
        "name": name,
        "assistant_id": str(experiment.assistant.id),
        "citations_enabled": experiment.citations_enabled,
        "input_formatter": experiment.input_formatter or "",
    }
    return assistant_params


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
