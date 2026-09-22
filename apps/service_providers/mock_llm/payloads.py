"""The OpenAI wire formats the mock answers in: envelopes and SSE events."""

import itertools
import json
import re
import time
import uuid

from .directives import ErrorDirective

MODEL_NAME = "stub"


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


def prompt_text(messages: list[dict]) -> str:
    return " ".join(_content_text(message.get("content")) for message in messages)


def input_items(payload_input) -> list[dict]:
    """The Responses API `input`, normalised to the message list the helpers above expect."""
    if isinstance(payload_input, str):
        return [{"role": "user", "content": payload_input}]
    return [item for item in payload_input if isinstance(item, dict)]


def _stream_pieces(text: str) -> list[str]:
    """Split into chunks that join back to exactly `text`."""
    return re.findall(r"\s+|\S+", text)


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


def chat_completion_envelope(*, model: str, text: str, usage: dict) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "created": int(time.time()),
        "model": model,
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": usage,
    }


def chat_completion_stream(*, envelope: dict, usage: dict, include_usage: bool):
    """SSE chunks for a streamed chat completion, built from the non-streamed envelope."""
    header = {key: envelope[key] for key in ("id", "created", "model")}
    text = envelope["choices"][0]["message"]["content"]

    def chunk(choices, extra=None):
        body = {**header, "object": "chat.completion.chunk", "choices": choices, **(extra or {})}
        return f"data: {json.dumps(body)}\n\n"

    yield chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
    for piece in _stream_pieces(text):
        yield chunk([{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
    yield chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}])
    if include_usage:
        yield chunk([], {"usage": usage})
    yield "data: [DONE]\n\n"


def responses_stream(envelope: dict):
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
