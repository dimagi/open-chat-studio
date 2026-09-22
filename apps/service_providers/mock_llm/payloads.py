"""The OpenAI wire formats the mock answers in: envelopes and SSE events."""

import itertools
import json
import re
import time
import uuid

from .directives import ErrorDirective

MODEL_NAME = "stub"

# Real OpenAI reports these as the error `type`; the rest fall back to the status family.
CODE_TYPES = {
    "insufficient_quota": "insufficient_quota",
    "credit_balance_exhausted": "insufficient_quota",
    "rate_limit_exceeded": "rate_limit_error",
    "invalid_api_key": "authentication_error",
}


def error_body(error: ErrorDirective) -> dict:
    """An OpenAI-shaped error body.

    Both `type` and `code` carry the reason because the OpenAI SDK surfaces them as
    separate attributes and OCS's error classification reads whichever is set.
    """
    family = "server_error" if error.status >= 500 else "invalid_request_error"
    return {
        "error": {
            "message": f"Mock LLM provider returned {error.status} ({error.code}) for an 'error' directive.",
            "type": CODE_TYPES.get(error.code, family),
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


def response_envelope(*, model: str, output: list[dict], input_tokens: int, output_tokens: int) -> dict:
    """A completed Responses API `response` object.

    These are the fields `openai.types.responses.Response` marks required.
    """
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": output,
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


def message_output(text: str) -> dict:
    """A Responses API assistant message carrying `text`."""
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def function_call_output(*, name: str, arguments: str) -> dict:
    """A Responses API function call item."""
    identifier = uuid.uuid4().hex
    return {
        "id": f"fc_{identifier}",
        "call_id": f"call_{identifier}",
        "type": "function_call",
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def chat_completion_envelope(*, model: str, usage: dict, text: str | None = None, tool_calls: list[dict] | None = None):
    """A chat completion, answering with either text or tool calls."""
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
        message["content"] = None
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "created": int(time.time()),
        "model": model,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage,
    }


def chat_tool_call(*, name: str, arguments: str) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def chat_completion_stream(*, envelope: dict, usage: dict, include_usage: bool):
    """SSE chunks for a streamed chat completion, built from the non-streamed envelope."""
    header = {key: envelope[key] for key in ("id", "created", "model")}
    choice = envelope["choices"][0]
    message = choice["message"]

    def chunk(choices, extra=None):
        body = {**header, "object": "chat.completion.chunk", "choices": choices, **(extra or {})}
        return f"data: {json.dumps(body)}\n\n"

    yield chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
    if tool_calls := message.get("tool_calls"):
        for position, call in enumerate(tool_calls):
            opening = {
                "index": position,
                "id": call["id"],
                "type": "function",
                "function": {"name": call["function"]["name"], "arguments": ""},
            }
            yield chunk([{"index": 0, "delta": {"tool_calls": [opening]}, "finish_reason": None}])
            for piece in _stream_pieces(call["function"]["arguments"]):
                argument_delta = {"index": position, "function": {"arguments": piece}}
                yield chunk([{"index": 0, "delta": {"tool_calls": [argument_delta]}, "finish_reason": None}])
    else:
        for piece in _stream_pieces(message["content"]):
            yield chunk([{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
    yield chunk([{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}])
    if include_usage:
        yield chunk([], {"usage": usage})
    yield "data: [DONE]\n\n"


def responses_stream(envelope: dict):
    """SSE events for a streamed Responses API call.

    Only the events langchain_openai reads: `response.created` for the id, the deltas for
    whichever output items the answer carries, and `response.completed`, which carries the
    whole envelope and so is where the final text and the usage numbers come from. Unlike
    chat completions there is no `[DONE]` sentinel; the stream ends when the connection
    closes.
    """
    sequence = itertools.count()

    def event(body: dict) -> str:
        body = {**body, "sequence_number": next(sequence)}
        return f"event: {body['type']}\ndata: {json.dumps(body)}\n\n"

    in_progress = {**envelope, "status": "in_progress", "output": [], "usage": None}
    yield event({"type": "response.created", "response": in_progress})

    for output_index, item in enumerate(envelope["output"]):
        yield event({"type": "response.output_item.added", "output_index": output_index, "item": item})
        if item["type"] == "function_call":
            position = {"item_id": item["id"], "output_index": output_index}
            for piece in _stream_pieces(item["arguments"]):
                yield event({"type": "response.function_call_arguments.delta", "delta": piece, **position})
            yield event(
                {
                    "type": "response.function_call_arguments.done",
                    "arguments": item["arguments"],
                    "name": item["name"],
                    **position,
                }
            )
        else:
            text = item["content"][0]["text"]
            position = {"item_id": item["id"], "output_index": output_index, "content_index": 0, "logprobs": []}
            for piece in _stream_pieces(text):
                yield event({"type": "response.output_text.delta", "delta": piece, **position})
            yield event({"type": "response.output_text.done", "text": text, **position})
        yield event({"type": "response.output_item.done", "output_index": output_index, "item": item})

    yield event({"type": "response.completed", "response": envelope})
