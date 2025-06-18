from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph

from apps.pipelines.const import STANDARD_OUTPUT_NAME
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes import nodes
from apps.pipelines.nodes.nodes import ToolConfigModel


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
    if edges is None:
        edges = _make_edges(nodes)
    if isinstance(edges[0], str):
        edges = _edges_from_strings(edges, nodes)
    flow_nodes = []
    for node in nodes:
        flow_nodes.append({"id": node["id"], "data": node})
    pipeline.data = {"edges": edges, "nodes": flow_nodes}
    pipeline.update_nodes_from_data()
    graph = PipelineGraph.build_from_pipeline(pipeline)
    return graph.build_runnable()


def start_node():
    return {"id": str(uuid4()), "type": nodes.StartNode.__name__, "params": {"name": "start"}}


def end_node():
    return {"id": str(uuid4()), "type": nodes.EndNode.__name__, "params": {"name": "end"}}


def email_node(name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "label": "Send an email",
        "type": "SendEmail",
        "params": {
            "name": name or "send email" + node_id[-4:],
            "recipient_list": "test@example.com",
            "subject": "This is an interesting email",
        },
    }


def llm_response_with_prompt_node(
    provider_id: str,
    provider_model_id: str,
    source_material_id: str | None = None,
    prompt: str | None = None,
    history_type: str | None = None,
    history_name: str | None = None,
    tool_config: dict[str, ToolConfigModel] | None = None,
    name: str | None = None,
    **kwargs,
):
    if prompt is None:
        prompt = "You are a helpful assistant"

    node_id = str(uuid4())
    params = {
        "name": name or "llm response with prompt" + node_id[-4:],
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

    return {
        "id": node_id,
        "type": "LLMResponseWithPrompt",
        "params": params | kwargs,
    }


def llm_response_node(provider_id: str, provider_model_id: str, name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.LLMResponse.__name__,
        "params": {
            "name": name or "llm response" + node_id[-4:],
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
        },
    }


def render_template_node(template_string: str | None = None, name: str | None = None):
    if template_string is None:
        template_string = "<b>{{ summary }}</b>"
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.RenderTemplate.__name__,
        "params": {
            "name": name or "render template" + node_id[-4:],
            "template_string": template_string,
        },
    }


def passthrough_node(name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.Passthrough.__name__,
        "params": {"name": name or "passthrough-" + node_id[-4:]},
    }


def boolean_node(input_equals="hello", name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.BooleanNode.__name__,
        "params": {"name": name or "boolean-" + node_id[-4:], "input_equals": input_equals},
    }


def router_node(provider_id: str, provider_model_id: str, keywords: list[str], name: str | None = None, **kwargs):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.RouterNode.__name__,
        "params": {
            **{
                "name": name or "router-" + node_id[-4:],
                "prompt": "You are a router",
                "keywords": keywords,
                "llm_provider_id": provider_id,
                "llm_provider_model_id": provider_model_id,
            },
            **kwargs,
        },
    }


def state_key_router_node(
    route_key: str, keywords: list[str], data_source="temp_state", tag_output=False, name: str | None = None
):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.StaticRouterNode.__name__,
        "params": {
            "name": name or "static router-" + node_id[-4:],
            "data_source": data_source,
            "route_key": route_key,
            "keywords": keywords,
            "tag_output_message": tag_output,
        },
    }


def assistant_node(assistant_id: str, name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.AssistantNode.__name__,
        "params": {
            "name": name or "assistant-" + node_id[-4:],
            "assistant_id": assistant_id,
            "citations_enabled": True,
            "input_formatter": "",
        },
    }


def extract_participant_data_node(
    provider_id: str, provider_model_id: str, data_schema: str, key_name: str, name: str | None = None
):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.ExtractParticipantData.__name__,
        "params": {
            "name": name or "extract participant data" + node_id[-4:],
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
            "key_name": key_name,
        },
    }


def extract_structured_data_node(provider_id: str, provider_model_id: str, data_schema: str, name: str | None = None):
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.ExtractStructuredData.__name__,
        "params": {
            "name": name or "extract structured data" + node_id[-4:],
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
        },
    }


def code_node(code: str | None = None, name: str | None = None):
    if code is None:
        code = "return f'Hello, {input}!'"
    node_id = str(uuid4())
    return {
        "id": node_id,
        "type": nodes.CodeNode.__name__,
        "params": {
            "name": name or "code node" + node_id[-4:],
            "code": code,
        },
    }
