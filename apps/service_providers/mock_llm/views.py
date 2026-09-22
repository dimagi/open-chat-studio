"""The OpenAI-compatible endpoints themselves. See the package docstring for the directives."""

import itertools
import json
import time
from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import payloads
from .directives import ErrorDirective, parse_directives

# Rotates the wording so consecutive replies in one conversation aren't identical.
responses_served = itertools.count()


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

    text = payloads.build_response_text(directives.length, index=next(responses_served))
    prompt_tokens = payloads.count_tokens(payloads.prompt_text(messages))
    completion_tokens = payloads.count_tokens(text)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    envelope = payloads.chat_completion_envelope(
        model=payload.get("model") or payloads.MODEL_NAME, text=text, usage=usage
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

    text = payloads.build_response_text(directives.length, index=next(responses_served))
    envelope = payloads.response_envelope(
        model=payload.get("model") or payloads.MODEL_NAME,
        text=text,
        input_tokens=payloads.count_tokens(payloads.prompt_text(items)),
        output_tokens=payloads.count_tokens(text),
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
