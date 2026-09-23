"""The OpenAI-compatible endpoints themselves. See the package docstring for the directives."""

import itertools
import json
import time
from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import lorem, payloads, schemas
from .directives import ErrorDirective, parse_directives

# Rotates the wording so consecutive replies in one conversation aren't identical.
_responses_served = itertools.count()


def next_response_index() -> int:
    return next(_responses_served)


def reset_response_index() -> None:
    """Restart the rotation. For tests, which need to know the index a reply was built from."""
    global _responses_served
    _responses_served = itertools.count()


def debug_only(view):
    """Serve the view only while DEBUG is on."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.DEBUG:
            raise Http404("The mock LLM provider is served only when DEBUG is on.")
        return view(request, *args, **kwargs)

    return wrapper


def _payload(request) -> dict:
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fail(error: ErrorDirective) -> JsonResponse:
    return JsonResponse(payloads.error_body(error), status=error.status)


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
    if not isinstance(tools, list) or not tools:
        return None
    flattened = [_flatten_tool(tool) for tool in tools if isinstance(tool, dict)]
    flattened = [tool for tool in flattened if tool.get("name")]
    if not flattened:
        return None

    if tool_choice == "required":
        return flattened[0]
    if isinstance(tool_choice, dict):
        wanted = tool_choice.get("name") or (tool_choice.get("function") or {}).get("name")
        return next((tool for tool in flattened if tool["name"] == wanted), flattened[0])
    return None


def _flatten_tool(tool: dict) -> dict:
    """Both `{type, function: {name, parameters}}` and the flat Responses API shape."""
    body = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return {"name": body.get("name"), "parameters": body.get("parameters") or {}}


def _json_schema(container) -> dict | None:
    """The schema out of a `response_format` or a Responses API `text.format`."""
    if not isinstance(container, dict) or container.get("type") != "json_schema":
        return None
    body = container.get("json_schema") if isinstance(container.get("json_schema"), dict) else container
    schema = body.get("schema")
    return schema if isinstance(schema, dict) else None


def _structured_text(schema: dict, index: int) -> str:
    return json.dumps(schemas.sample(schema, index=index))


@csrf_exempt
@debug_only
@require_POST
def chat_completions(request):
    payload = _payload(request)
    messages = payload.get("messages")
    if not messages:
        return _fail(ErrorDirective(status=400, code="invalid_request_error"))

    directives = parse_directives(payloads.last_user_message(messages))
    if failure := _apply(directives):
        return failure

    index = next_response_index()
    tool = _forced_tool(payload.get("tools"), payload.get("tool_choice"))
    schema = _json_schema(payload.get("response_format"))
    tool_calls = None
    if tool:
        text = None
        tool_calls = [payloads.chat_tool_call(name=tool["name"], arguments=_structured_text(tool["parameters"], index))]
        completion_text = tool_calls[0]["function"]["arguments"]
    elif schema:
        text = completion_text = _structured_text(schema, index)
    else:
        text = completion_text = lorem.build_response_text(directives.length, index=index)

    prompt_tokens = lorem.count_tokens(payloads.prompt_text(messages))
    completion_tokens = lorem.count_tokens(completion_text)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    envelope = payloads.chat_completion_envelope(
        model=payload.get("model") or payloads.MODEL_NAME, text=text, tool_calls=tool_calls, usage=usage
    )

    if payload.get("stream"):
        include_usage = bool((payload.get("stream_options") or {}).get("include_usage"))
        return StreamingHttpResponse(
            payloads.chat_completion_stream(envelope=envelope, usage=usage, include_usage=include_usage),
            content_type="text/event-stream",
        )

    return JsonResponse(envelope)


@csrf_exempt
@debug_only
@require_POST
def responses(request):
    payload = _payload(request)
    if not payload.get("input"):
        return _fail(ErrorDirective(status=400, code="invalid_request_error"))
    items = payloads.input_items(payload["input"])

    directives = parse_directives(payloads.last_user_message(items))
    if failure := _apply(directives):
        return failure

    index = next_response_index()
    tool = _forced_tool(payload.get("tools"), payload.get("tool_choice"))
    schema = _json_schema((payload.get("text") or {}).get("format"))
    if tool:
        arguments = _structured_text(tool["parameters"], index)
        output = [payloads.function_call_output(name=tool["name"], arguments=arguments)]
        output_text = arguments
    else:
        output_text = (
            _structured_text(schema, index) if schema else lorem.build_response_text(directives.length, index=index)
        )
        output = [payloads.message_output(output_text)]

    envelope = payloads.response_envelope(
        model=payload.get("model") or payloads.MODEL_NAME,
        output=output,
        input_tokens=lorem.count_tokens(payloads.prompt_text(items)),
        output_tokens=lorem.count_tokens(output_text),
    )

    if payload.get("stream"):
        return StreamingHttpResponse(payloads.responses_stream(envelope), content_type="text/event-stream")

    return JsonResponse(envelope)


@debug_only
@require_GET
def models(request):
    return JsonResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": payloads.MODEL_NAME,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "mock-llm",
                }
            ],
        }
    )
