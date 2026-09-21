#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "flask>=3.1.3",
# ]
# ///
"""
Mock LLM provider for Open Chat Studio local development.

Serves the OpenAI-compatible endpoints OCS calls and answers with lorem ipsum, so a dev
environment can exercise chatbots and pipelines end to end without spending money or
needing an API key.

Endpoints:
    /v1/responses          the Responses API, which OCS's "openai" provider type uses
    /v1/chat/completions    used by the provider types that route through
                            OpenAIGenericService, such as LiteLLM, Groq and OpenRouter
    /v1/models

Usage:
    uv run scripts/mock_llm_server.py            # listens on http://localhost:9100

Wiring it into OCS:
    `manage.py bootstrap_data` creates a provider named "Stub LLM" pointing here, along
    with a model named "stub". To add one by hand instead: Service Providers > LLM > add,
    choose type "OpenAI", set API Base URL to http://localhost:9100/v1 and any non-empty
    API key, then add a custom model named "stub".

Directives:
    The last user message is scanned, case-insensitively and on word boundaries, for the
    keywords below. They combine freely, e.g. "slow 3 long" or "slow 2 error 429".

    short                 reply with one sentence
    medium                reply with two paragraphs (the default)
    long                  reply with six paragraphs
    slow                  wait 5 seconds before replying
    slow <n>              wait <n> seconds before replying
    error                 fail with HTTP 500
    error <status>        fail with that HTTP status
    error <code>          fail with the status that code implies, one of:
                          insufficient_quota, credit_balance_exhausted,
                          rate_limit_exceeded, invalid_api_key, model_not_found,
                          context_length_exceeded

    The error codes are the ones apps/service_providers/llm_service/error_classification.py
    reads, so they are the way to drive OCS down its billing, authentication, not-found and
    context-overflow branches.

    Keywords are matched anywhere in the message, so an ordinary question that happens to
    contain "how long..." will get a long answer. That is the trade for not needing a
    prefix.

Limitations:
    Tool calls are ignored: a request carrying `tools` still gets a plain text answer, so
    a pipeline that depends on the model choosing a tool won't take that branch. There is
    no embeddings endpoint.

Running it under systemd as a user service:
    See the install instructions at the top of scripts/mock-llm-server.service.
"""

import argparse
import itertools
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass

DEFAULT_PORT = 9100
DEFAULT_HOST = "127.0.0.1"
DEFAULT_SLOW_SECONDS = 5.0
DEFAULT_ERROR_STATUS = 500
MODEL_NAME = "stub"
SENTENCES_PER_PARAGRAPH = 5
CHARS_PER_TOKEN = 4

SENTENCE_COUNTS = {"short": 1, "medium": 10, "long": 30}
DEFAULT_LENGTH = "medium"

# Error codes whose HTTP status is implied, so "error insufficient_quota" is enough.
CODE_STATUSES = {
    "insufficient_quota": 429,
    "credit_balance_exhausted": 429,
    "rate_limit_exceeded": 429,
    "invalid_api_key": 401,
    "model_not_found": 404,
    "context_length_exceeded": 400,
}
# The code to report when the directive named a status instead.
STATUS_CODES = {
    400: "invalid_request_error",
    401: "invalid_api_key",
    403: "permission_denied",
    404: "model_not_found",
    408: "request_timeout",
    429: "rate_limit_exceeded",
}

_LENGTH_RE = re.compile(r"\b(short|medium|long)\b", re.IGNORECASE)
_SLOW_RE = re.compile(r"\bslow\b(?:\s+(\d+(?:\.\d+)?))?", re.IGNORECASE)
_ERROR_RE = re.compile(rf"\berror\b(?:\s+(\d{{3}}|{'|'.join(CODE_STATUSES)}))?", re.IGNORECASE)

_LOREM_TEXT = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et "
    "dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea "
    "commodo consequat duis aute irure in reprehenderit voluptate velit esse cillum eu fugiat nulla pariatur "
    "excepteur sint occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est laborum"
)
_LOREM_WORDS = _LOREM_TEXT.split()


@dataclass(frozen=True)
class ErrorDirective:
    status: int
    code: str


@dataclass(frozen=True)
class Directives:
    length: str = DEFAULT_LENGTH
    delay_seconds: float = 0.0
    error: ErrorDirective | None = None


def parse_directives(text: str) -> Directives:
    """Read the response-shaping keywords out of a user message."""
    lengths = _LENGTH_RE.findall(text)
    slow = _SLOW_RE.search(text)
    return Directives(
        # Last one wins, so "short, no wait, make it long" does what it says.
        length=lengths[-1].lower() if lengths else DEFAULT_LENGTH,
        delay_seconds=float(slow.group(1)) if slow and slow.group(1) else (DEFAULT_SLOW_SECONDS if slow else 0.0),
        error=_parse_error(text),
    )


def _parse_error(text: str) -> ErrorDirective | None:
    match = _ERROR_RE.search(text)
    if not match:
        return None
    argument = (match.group(1) or "").lower()
    if argument in CODE_STATUSES:
        return ErrorDirective(status=CODE_STATUSES[argument], code=argument)
    status = int(argument) if argument else DEFAULT_ERROR_STATUS
    return ErrorDirective(status=status, code=_code_for_status(status))


def _code_for_status(status: int) -> str:
    if code := STATUS_CODES.get(status):
        return code
    return "server_error" if status >= 500 else "invalid_request_error"


def error_body(error: ErrorDirective) -> dict:
    """An OpenAI-shaped error body.

    Both `type` and `code` carry the reason because the OpenAI SDK surfaces them as
    separate attributes and OCS's error classification reads whichever is set.
    """
    family = "server_error" if error.status >= 500 else "invalid_request_error"
    return {
        "error": {
            "message": f"Mock LLM provider returned {error.status} ({error.code}) for an 'error' directive.",
            "type": family,
            "code": error.code,
        }
    }


def _sentence(index: int) -> str:
    """A lorem sentence that is the same every time for a given index."""
    rng = random.Random(index)
    words = [rng.choice(_LOREM_WORDS) for _ in range(rng.randint(8, 18))]
    return f"{words[0].capitalize()} {' '.join(words[1:])}."


def build_response_text(length: str, index: int = 0) -> str:
    """Lorem ipsum of the requested size. `index` rotates the wording between calls."""
    total = SENTENCE_COUNTS[length]
    sentences = [_sentence(index * total + offset) for offset in range(total)]
    paragraphs = [
        " ".join(sentences[start : start + SENTENCES_PER_PARAGRAPH])
        for start in range(0, total, SENTENCES_PER_PARAGRAPH)
    ]
    return "\n\n".join(paragraphs)


def count_tokens(text: str) -> int:
    """A rough token count, so OCS's cost tracking records something plausible."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _content_text(content) -> str:
    """Flatten a message's content to plain text.

    Any part carrying a `text` key counts, which covers the `text` of chat completions and
    the `input_text`/`output_text` of the Responses API without enumerating part types.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def last_user_message(messages: list[dict]) -> str:
    """The text of the most recent user turn, or "" if there isn't one."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_text(message.get("content"))
    return ""


def _prompt_text(messages: list[dict]) -> str:
    return " ".join(_content_text(message.get("content")) for message in messages)


def _stream_pieces(text: str) -> list[str]:
    """Split into chunks that join back to exactly `text`."""
    return re.findall(r"\s+|\S+", text)


def _input_items(payload_input) -> list[dict]:
    """The Responses API `input`, normalised to the message list the helpers above expect."""
    if isinstance(payload_input, str):
        return [{"role": "user", "content": payload_input}]
    return [item for item in payload_input if isinstance(item, dict)]


def response_envelope(*, model: str, text: str, input_tokens: int, output_tokens: int) -> dict:
    """A completed Responses API `response` object.

    Every key here is one the OpenAI SDK's `Response` model requires, so dropping any of
    them makes the client raise before OCS ever sees the text.
    """
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens + output_tokens,
        },
    }


def create_app():
    """Build the Flask app."""
    # flask is a PEP 723 script dependency and absent from the project venv, so a top-level
    # import would make everything above unimportable by the tests.
    from flask import Flask, Response, jsonify, request  # noqa: PLC0415  # ty: ignore[unresolved-import]

    app = Flask(__name__)
    # Held per app rather than module-wide so each test client rotates from a known start.
    responses_served = itertools.count()

    def _fail(error: ErrorDirective):
        return jsonify(error_body(error)), error.status

    @app.post("/v1/chat/completions")
    def chat_completions():
        payload = request.get_json(silent=True) or {}
        messages = payload.get("messages")
        if not messages:
            return _fail(ErrorDirective(status=400, code="invalid_request_error"))

        directives = parse_directives(last_user_message(messages))
        if directives.delay_seconds:
            time.sleep(directives.delay_seconds)
        if directives.error:
            return _fail(directives.error)

        model = payload.get("model") or MODEL_NAME
        text = build_response_text(directives.length, index=next(responses_served))
        prompt_tokens = count_tokens(_prompt_text(messages))
        completion_tokens = count_tokens(text)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        envelope = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(time.time()),
            "model": model,
        }

        if payload.get("stream"):
            include_usage = bool((payload.get("stream_options") or {}).get("include_usage"))
            return Response(
                _stream(envelope=envelope, text=text, usage=usage, include_usage=include_usage),
                mimetype="text/event-stream",
            )

        return jsonify(
            {
                **envelope,
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": usage,
            }
        )

    @app.post("/v1/responses")
    def responses():
        payload = request.get_json(silent=True) or {}
        if not payload.get("input"):
            return _fail(ErrorDirective(status=400, code="invalid_request_error"))
        items = _input_items(payload["input"])

        directives = parse_directives(last_user_message(items))
        if directives.delay_seconds:
            time.sleep(directives.delay_seconds)
        if directives.error:
            return _fail(directives.error)

        text = build_response_text(directives.length, index=next(responses_served))
        envelope = response_envelope(
            model=payload.get("model") or MODEL_NAME,
            text=text,
            input_tokens=count_tokens(_prompt_text(items)),
            output_tokens=count_tokens(text),
        )

        if payload.get("stream"):
            return Response(_stream_responses(envelope), mimetype="text/event-stream")

        return jsonify(envelope)

    @app.get("/v1/models")
    def models():
        return jsonify(
            {
                "object": "list",
                "data": [
                    {
                        "id": MODEL_NAME,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "mock-llm-server",
                    }
                ],
            }
        )

    return app


def _stream(*, envelope: dict, text: str, usage: dict, include_usage: bool):
    def chunk(choices, extra=None):
        body = {**envelope, "object": "chat.completion.chunk", "choices": choices, **(extra or {})}
        return f"data: {json.dumps(body)}\n\n"

    yield chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
    for piece in _stream_pieces(text):
        yield chunk([{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
    yield chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}])
    if include_usage:
        yield chunk([], {"usage": usage})
    yield "data: [DONE]\n\n"


def _stream_responses(envelope: dict):
    """SSE events for a streamed Responses API call.

    Only the events langchain_openai reads: `response.created` for the id, the text
    deltas, and `response.completed`, which carries the whole envelope and so is where
    the final text and the usage numbers come from. Unlike chat completions there is no
    `[DONE]` sentinel; the stream ends when the connection closes.
    """
    message = envelope["output"][0]
    text = message["content"][0]["text"]
    sequence = itertools.count()

    def event(body: dict) -> str:
        body = {**body, "sequence_number": next(sequence)}
        return f"event: {body['type']}\ndata: {json.dumps(body)}\n\n"

    position = {"item_id": message["id"], "output_index": 0, "content_index": 0, "logprobs": []}
    in_progress = {**envelope, "status": "in_progress", "output": [], "usage": None}
    yield event({"type": "response.created", "response": in_progress})
    for piece in _stream_pieces(text):
        yield event({"type": "response.output_text.delta", "delta": piece, **position})
    yield event({"type": "response.output_text.done", "text": text, **position})
    yield event({"type": "response.completed", "response": envelope})


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock LLM provider for OCS local development")
    parser.add_argument("--host", default=os.environ.get("MOCK_LLM_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_LLM_PORT", DEFAULT_PORT)))
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    print(f"Mock LLM provider on {base_url}")
    print("  OCS provider type : OpenAI")
    print(f"  API Base URL      : {base_url}/v1")
    print("  API key           : anything non-empty")
    print(f"  Model             : {MODEL_NAME}")
    print()
    print("Say 'short', 'medium' or 'long' to size the reply; 'slow 3' to delay it; 'error 429' to fail it.")

    create_app().run(host=args.host, port=args.port, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
