from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes import nodes


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


def create_runnable(
    pipeline: Pipeline, nodes: list[dict], edges: list[dict] | None = None, lenient=False
) -> CompiledStateGraph:
    if edges is None:
        edges = _make_edges(nodes)
    flow_nodes = []
    for node in nodes:
        flow_nodes.append({"id": node["id"], "data": node})
    pipeline.data = {"edges": edges, "nodes": flow_nodes}
    pipeline.update_nodes_from_data()
    graph = PipelineGraph.build_from_pipeline(pipeline)
    graph.lenient_validation = lenient
    return graph.build_runnable()


def start_node():
    return {"id": str(uuid4()), "type": nodes.StartNode.__name__, "params": {"name": "start"}}


def end_node():
    return {"id": str(uuid4()), "type": nodes.EndNode.__name__, "params": {"name": "end"}}


def email_node():
    return {
        "id": str(uuid4()),
        "label": "Send an email",
        "type": "SendEmail",
        "params": {
            "name": "email",
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
    **kwargs,
):
    if prompt is None:
        prompt = "You are a helpful assistant"

    params = {
        "name": "llm response with prompt",
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

    return {
        "id": str(uuid4()),
        "type": "LLMResponseWithPrompt",
        "params": params | kwargs,
    }


def llm_response_node(provider_id: str, provider_model_id: str):
    return {
        "id": str(uuid4()),
        "type": nodes.LLMResponse.__name__,
        "params": {
            "name": "llm response",
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
        },
    }


def render_template_node(template_string: str | None = None):
    if template_string is None:
        template_string = "<b>{{ summary }}</b>"
    return {
        "id": str(uuid4()),
        "type": nodes.RenderTemplate.__name__,
        "params": {
            "name": "render template",
            "template_string": template_string,
        },
    }


def passthrough_node():
    return {
        "id": str(uuid4()),
        "type": nodes.Passthrough.__name__,
        "params": {"name": "passthrough"},
    }


def boolean_node(input_equals="hello"):
    return {
        "id": str(uuid4()),
        "type": nodes.BooleanNode.__name__,
        "params": {"name": "boolean", "input_equals": input_equals},
    }


def router_node(provider_id: str, provider_model_id: str, keywords: list[str], **kwargs):
    return {
        "id": str(uuid4()),
        "type": nodes.RouterNode.__name__,
        "params": {
            **{
                "name": "router",
                "prompt": "You are a router",
                "keywords": keywords,
                "llm_provider_id": provider_id,
                "llm_provider_model_id": provider_model_id,
            },
            **kwargs,
        },
    }


def state_key_router_node(route_key: str, keywords: list[str], data_source="temp_state", tag_output=False):
    return {
        "id": str(uuid4()),
        "type": nodes.StaticRouterNode.__name__,
        "params": {
            "name": "static router",
            "data_source": data_source,
            "route_key": route_key,
            "keywords": keywords,
            "tag_output_message": tag_output,
        },
    }


def assistant_node(assistant_id: str):
    return {
        "id": str(uuid4()),
        "type": nodes.AssistantNode.__name__,
        "params": {
            "name": "assistant",
            "assistant_id": assistant_id,
            "citations_enabled": True,
            "input_formatter": "",
        },
    }


def extract_participant_data_node(provider_id: str, provider_model_id: str, data_schema: str, key_name: str):
    return {
        "id": str(uuid4()),
        "type": nodes.ExtractParticipantData.__name__,
        "params": {
            "name": "extract participant data",
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
            "key_name": key_name,
        },
    }


def extract_structured_data_node(provider_id: str, provider_model_id: str, data_schema: str):
    return {
        "id": str(uuid4()),
        "type": nodes.ExtractStructuredData.__name__,
        "params": {
            "name": "extract structured data",
            "llm_provider_id": provider_id,
            "llm_provider_model_id": provider_model_id,
            "data_schema": data_schema,
        },
    }


def code_node(code: str | None = None):
    if code is None:
        code = "return f'Hello, {input}!'"
    return {
        "id": str(uuid4()),
        "type": nodes.CodeNode.__name__,
        "params": {
            "name": "code node",
            "code": code,
        },
    }
