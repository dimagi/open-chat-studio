"""The OpenAI-compatible endpoints themselves. See the package docstring for the directives."""

import itertools
import json
import time

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import MODEL_NAME, lorem, payloads, schemas
from .directives import ErrorDirective, parse_directives

# Rotates the wording so consecutive replies in one conversation aren't identical.
_responses_served = itertools.count()


def next_response_index() -> int:
    return next(_responses_served)


def reset_response_index() -> None:
    """Restart the rotation. For tests, which need to know the index a reply was built from."""
    global _responses_served
    _responses_served = itertools.count()


def _payload(request) -> dict:
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _dict(value) -> dict:
    """`value` if it is a JSON object, else an empty one, so a malformed field reads as absent."""
    return value if isinstance(value, dict) else {}


def _fail(error: ErrorDirective) -> JsonResponse:
    return JsonResponse(data=payloads.error_body(error), status=error.status)


def _bad_request() -> JsonResponse:
    return _fail(ErrorDirective(status=400, code="invalid_request_error"))


def _apply(directives) -> JsonResponse | None:
    """Carry out the delay and the failure a message asked for. Returns the failure, if any."""
    if directives.delay_seconds:
        time.sleep(directives.delay_seconds)
    return _fail(directives.error) if directives.error else None


def _forced_tool(tools, tool_choice) -> dict | None:
    """The tool the request insists on, flattened to {name, parameters}.

    Only a forced choice produces a call. Under "auto" the mock answers with text, which
    is what a model that saw no reason to use a tool would do.
    """
    named = _named_tools(tools)
    if not named:
        return None

    if tool_choice == "required":
        return named[0]
    if isinstance(tool_choice, dict):
        wanted = _chosen_tool_name(tool_choice)
        return next((tool for tool in named if tool["name"] == wanted), named[0])
    return None


def _named_tools(tools) -> list[dict]:
    """The request's tools, flattened, leaving out any without a name."""
    if not isinstance(tools, list):
        return []
    flattened = [_flatten_tool(tool) for tool in tools if isinstance(tool, dict)]
    return [tool for tool in flattened if tool["name"]]


def _chosen_tool_name(tool_choice: dict) -> str | None:
    """Both `{type, function: {name}}` and the flat Responses API `{type, name}`."""
    return tool_choice.get("name") or (tool_choice.get("function") or {}).get("name")


def _flatten_tool(tool: dict) -> dict:
    """Both `{type, function: {name, parameters}}` and the flat Responses API shape."""
    body = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return {"name": body.get("name"), "parameters": body.get("parameters") or {}}


def _json_schema(container) -> dict | None:
    """The schema out of a `response_format` or a Responses API `text.format`."""
    container = _dict(container)
    if container.get("type") != "json_schema":
        return None
    body = _dict(container.get("json_schema")) or container
    return _dict(body.get("schema")) or None


def _structured_text(schema: dict, index: int) -> str:
    return json.dumps(schemas.sample(schema=schema, index=index))


@csrf_exempt
@require_POST
def chat_completions(request):
    payload = _payload(request)
    messages = _messages(payload)
    if messages is None:
        return _bad_request()

    directives = parse_directives(payloads.last_user_message(messages))
    if failure := _apply(directives):
        return failure

    text, tool_calls, completion_text = _chat_answer(
        payload=payload, length=directives.length, index=next_response_index()
    )

    prompt_tokens = lorem.count_tokens(payloads.prompt_text(messages))
    completion_tokens = lorem.count_tokens(completion_text)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    envelope = payloads.chat_completion_envelope(
        model=payload.get("model") or MODEL_NAME, text=text, tool_calls=tool_calls, usage=usage
    )

    if payload.get("stream"):
        include_usage = bool(_dict(payload.get("stream_options")).get("include_usage"))
        return StreamingHttpResponse(
            streaming_content=payloads.chat_completion_stream(
                envelope=envelope, usage=usage, include_usage=include_usage
            ),
            content_type="text/event-stream",
        )

    return JsonResponse(envelope)


def _messages(payload: dict) -> list[dict] | None:
    """The request's chat messages, or None when they are missing or not a list of objects."""
    messages = payload.get("messages")
    if messages and isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
        return messages
    return None


def _chat_answer(payload: dict, length: str, index: int) -> tuple[str | None, list[dict] | None, str]:
    """The message text, the tool calls, and the text whose tokens count as the completion."""
    if tool := _forced_tool(tools=payload.get("tools"), tool_choice=payload.get("tool_choice")):
        arguments = _structured_text(schema=tool["parameters"], index=index)
        return None, [payloads.chat_tool_call(name=tool["name"], arguments=arguments)], arguments
    if schema := _json_schema(payload.get("response_format")):
        text = _structured_text(schema=schema, index=index)
    else:
        text = lorem.build_response_text(length=length, index=index)
    return text, None, text


@csrf_exempt
@require_POST
def responses(request):
    payload = _payload(request)
    if not payload.get("input") or not isinstance(payload["input"], str | list):
        return _bad_request()
    items = payloads.input_items(payload["input"])

    directives = parse_directives(payloads.last_user_message(items))
    if failure := _apply(directives):
        return failure

    index = next_response_index()
    tool = _forced_tool(tools=payload.get("tools"), tool_choice=payload.get("tool_choice"))
    schema = _json_schema(_dict(payload.get("text")).get("format"))
    if tool:
        arguments = _structured_text(schema=tool["parameters"], index=index)
        output = [payloads.function_call_output(name=tool["name"], arguments=arguments)]
        output_text = arguments
    else:
        output_text = (
            _structured_text(schema=schema, index=index)
            if schema
            else lorem.build_response_text(length=directives.length, index=index)
        )
        output = [payloads.message_output(output_text)]

    envelope = payloads.response_envelope(
        model=payload.get("model") or MODEL_NAME,
        output=output,
        input_tokens=lorem.count_tokens(payloads.prompt_text(items)),
        output_tokens=lorem.count_tokens(output_text),
    )

    if payload.get("stream"):
        return StreamingHttpResponse(
            streaming_content=payloads.responses_stream(envelope), content_type="text/event-stream"
        )

    return JsonResponse(envelope)


@require_GET
def models(request):
    return JsonResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": MODEL_NAME,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "mock-llm",
                }
            ],
        }
    )
