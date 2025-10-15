from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph

from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes import nodes
from apps.pipelines.nodes.nodes import AnthropicWebSearchToolConfig, BuiltinToolConfig
from apps.utils.factories.pipelines import PipelineFactory


def _make_edges(nodes) -> list[dict]:
    if len(nodes) <= 1:
        return []

    return [
        {
            "id": f"{node['id']}->{nodes[i + 1]['id']}",
            "source": node["id"],
            "target": nodes[i + 1]["id"],
        }
        for i, node in enumerate(nodes[:-1])
    ]


def _edges_from_strings(edge_strings: list[str], nodes: list[dict]) -> list[dict]:
    """
    Convert a list of edge strings into a list of edge dictionaries.

    Each edge string should be in the format "source - target" or "source:handle_index - target".
    """
    nodes_by_name = {node["params"]["name"]: node for node in nodes}
    edges = []
    for edge in edge_strings:
        source, target = edge.split(" - ")
        source_handle_index = None
        if ":" in source:
            source, source_handle_index = source.split(":")
        if source not in nodes_by_name or target not in nodes_by_name:
            raise ValueError(f"Invalid edge: {edge}")
        source_node = nodes_by_name[source]
        target_node = nodes_by_name[target]
        edges.append(
            {
                "id": f"{source} -> {target}",
                "source": source_node["id"],
                "target": target_node["id"],
                "sourceHandle": f"output_{source_handle_index}"
                if source_handle_index is not None
                else STANDARD_OUTPUT_NAME,
            }
        )
    return edges


def create_runnable(pipeline: Pipeline, nodes: list[dict], edges: list[dict | str] | None = None) -> CompiledStateGraph:
    pipeline = create_pipeline_model(nodes, edges, pipeline)
    graph = PipelineGraph.build_from_pipeline(pipeline)
    return graph.build_runnable()


def create_pipeline_model(
    nodes: list[dict], edges: list[dict | str] | None = None, pipeline: Pipeline = None
) -> Pipeline:
    if not pipeline:
        pipeline = PipelineFactory()
    if edges is None:
        edges = _make_edges(nodes)
    if edges and isinstance(edges[0], str):
        edges = _edges_from_strings(edges, nodes)
    flow_nodes = []
    for node in nodes:
        flow_nodes.append({"id": node["id"], "data": node})
    pipeline.data = {"edges": edges, "nodes": flow_nodes}
    pipeline.update_nodes_from_data()
    return pipeline


def start_node():
    return {"id": _node_id("start"), "type": nodes.StartNode.__name__, "params": {"name": "start"}}


def end_node():
    return {"id": _node_id("end"), "type": nodes.EndNode.__name__, "params": {"name": "end"}}


def email_node(name: str | None = None):
    return _with_node_id_and_name(
        name,
        "send_email",
        {
            "label": "Send an email",
            "type": "SendEmail",
            "params": {
                "recipient_list": "test@example.com",
                "subject": "This is an interesting email",
            },
        },
    )


def llm_response_with_prompt_node(
    provider_id: str,
    provider_model_id: str,
    source_material_id: str | None = None,
    prompt: str | None = None,
    history_type: str | None = None,
    history_name: str | None = None,
    tool_config: dict[str, BuiltinToolConfig | AnthropicWebSearchToolConfig] | None = None,
    name: str | None = None,
    **kwargs,
):
    if prompt is None:
        prompt = "You are a helpful assistant"

    params = {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": provider_model_id,
        "prompt": prompt,
    }
    if source_material_id is not None:
        params["source_material_id"] = source_material_id

    if history_type is not None:
        params["history_type"] = history_type

    if history_name is not None:
        params["history_name"] = history_name

    params["tool_config"] = tool_config or {}

    return _with_node_id_and_name(
        name,
        "llm",
        {
            "type": "LLMResponseWithPrompt",
            "params": params | kwargs,
        },
    )


def llm_response_node(provider_id: str, provider_model_id: str, name: str | None = None):
    return _with_node_id_and_name(
        name,
        "llm_response",
        {
            "type": nodes.LLMResponse.__name__,
            "params": {
                "llm_provider_id": provider_id,
                "llm_provider_model_id": provider_model_id,
            },
        },
    )


def render_template_node(template_string: str | None = None, name: str | None = None):
    if template_string is None:
        template_string = "<b>{{ summary }}</b>"
    return _with_node_id_and_name(
        name,
        "render-template",
        {
            "type": nodes.RenderTemplate.__name__,
            "params": {
                "template_string": template_string,
            },
        },
    )


def passthrough_node(name: str | None = None):
    return _with_node_id_and_name(name, "passthrough", {"type": nodes.Passthrough.__name__, "params": {}})


def boolean_node(input_equals="hello", name: str | None = None):
    return _with_node_id_and_name(
        name,
        "boolean",
        {
            "type": nodes.BooleanNode.__name__,
            "params": {"input_equals": input_equals},
        },
    )


def router_node(provider_id: str, provider_model_id: str, keywords: list[str], name: str | None = None, **kwargs):
    return _with_node_id_and_name(
        name,
        "router",
        {
            "type": nodes.RouterNode.__name__,
            "params": {
                **{
                    "prompt": "You are a router",
                    "keywords": keywords,
                    "llm_provider_id": provider_id,
                    "llm_provider_model_id": provider_model_id,
                },
                **kwargs,
            },
        },
    )


def state_key_router_node(
    route_key: str, keywords: list[str], data_source="temp_state", tag_output=False, name: str | None = None
):
    return _with_node_id_and_name(
        name,
        "static_router",
        {
            "type": nodes.StaticRouterNode.__name__,
            "params": {
                "data_source": data_source,
                "route_key": route_key,
                "keywords": keywords,
                "tag_output_message": tag_output,
            },
        },
    )


def assistant_node(assistant_id: str, name: str | None = None):
    return _with_node_id_and_name(
        name,
        "assistant",
        {
            "type": nodes.AssistantNode.__name__,
            "params": {
                "assistant_id": assistant_id,
                "citations_enabled": True,
                "input_formatter": "",
            },
        },
    )


def extract_participant_data_node(
    provider_id: str, provider_model_id: str, data_schema: str, key_name: str, name: str | None = None
):
    return _with_node_id_and_name(
        name,
        "extract_participant_data",
        {
            "type": nodes.ExtractParticipantData.__name__,
            "params": {
                "llm_provider_id": provider_id,
                "llm_provider_model_id": provider_model_id,
                "data_schema": data_schema,
                "key_name": key_name,
            },
        },
    )


def extract_structured_data_node(provider_id: str, provider_model_id: str, data_schema: str, name: str | None = None):
    return _with_node_id_and_name(
        name,
        "extract_structured_data",
        {
            "type": nodes.ExtractStructuredData.__name__,
            "params": {
                "llm_provider_id": provider_id,
                "llm_provider_model_id": provider_model_id,
                "data_schema": data_schema,
            },
        },
    )


def code_node(code: str | None = None, name: str | None = None):
    if code is None:
        code = "return f'Hello, {input}!'"
    return _with_node_id_and_name(
        name,
        "code",
        {
            "type": nodes.CodeNode.__name__,
            "params": {
                "code": code,
            },
        },
    )


def _with_node_id_and_name(name: str, default_name: str, params: dict):
    node_id = _node_id(name, default_name)
    params["id"] = node_id
    params["params"]["name"] = name or node_id
    return params


def _node_id(name: str, default_name: str = None):
    name = name or default_name or str(uuid4())
    return f"{name}-{str(uuid4())[-4:]}"
