import importlib
import json

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import Resolver404, clear_url_caches, resolve
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.responses import Response, ResponseStreamEvent
from pydantic import TypeAdapter

from apps.service_providers.mock_llm import schemas, views
from apps.service_providers.mock_llm.directives import (
    DEFAULT_SLOW_SECONDS,
    MAX_SLOW_SECONDS,
    Directives,
    ErrorDirective,
    parse_directives,
)
from apps.service_providers.mock_llm.lorem import (
    SENTENCES_PER_PARAGRAPH,
    build_response_text,
)
from apps.service_providers.mock_llm.payloads import (
    MODEL_NAME,
    error_body,
    last_user_message,
)


@pytest.fixture(autouse=True)
def _debug_on(settings):
    settings.DEBUG = True


@pytest.fixture(autouse=True)
def _reset_rotation():
    """Restart the wording rotation, so each test knows which `index` its reply was built from."""
    views.reset_response_index()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("hello there", Directives(), id="no-directives"),
        pytest.param("keep it short", Directives(length="short"), id="short"),
        pytest.param("make it LONG please", Directives(length="long"), id="case-insensitive"),
        pytest.param("shortly", Directives(), id="word-boundary"),
        pytest.param("short, no wait, make it long", Directives(length="long"), id="last-length-wins"),
        pytest.param("slow", Directives(delay_seconds=DEFAULT_SLOW_SECONDS), id="slow-default"),
        pytest.param("slow 3", Directives(delay_seconds=3.0), id="slow-seconds"),
        pytest.param("slow 2.5", Directives(delay_seconds=2.5), id="slow-fractional"),
        pytest.param(
            "error",
            Directives(error=ErrorDirective(status=500, code="server_error")),
            id="error-default",
        ),
        pytest.param(
            "error 429",
            Directives(error=ErrorDirective(status=429, code="rate_limit_exceeded")),
            id="error-known-status",
        ),
        pytest.param(
            "error 418",
            Directives(error=ErrorDirective(status=418, code="invalid_request_error")),
            id="error-unmapped-4xx",
        ),
        pytest.param(
            "error 503",
            Directives(error=ErrorDirective(status=503, code="server_error")),
            id="error-unmapped-5xx",
        ),
        pytest.param(
            "error insufficient_quota",
            Directives(error=ErrorDirective(status=429, code="insufficient_quota")),
            id="error-code-implies-status",
        ),
        pytest.param(
            "error invalid_api_key",
            Directives(error=ErrorDirective(status=401, code="invalid_api_key")),
            id="error-auth-code",
        ),
        pytest.param(
            "slow 3 long",
            Directives(length="long", delay_seconds=3.0),
            id="combined",
        ),
    ],
)
def test_parse_directives(text, expected):
    assert parse_directives(text) == expected


@pytest.mark.parametrize(
    ("status", "family"),
    [
        pytest.param(429, "invalid_request_error", id="client-error"),
        pytest.param(500, "server_error", id="server-error"),
    ],
)
def test_error_body_family(status, family):
    body = error_body(ErrorDirective(status=status, code="whatever"))
    assert body["error"]["type"] == family
    assert body["error"]["code"] == "whatever"


@pytest.mark.parametrize(
    ("length", "sentences"),
    [
        pytest.param("short", 1, id="short"),
        pytest.param("medium", 10, id="medium"),
        pytest.param("long", 30, id="long"),
    ],
)
def test_build_response_text_size(length, sentences):
    text = build_response_text(length)
    paragraphs = text.split("\n\n")
    assert len(paragraphs) == -(-sentences // SENTENCES_PER_PARAGRAPH)
    assert sum(paragraph.count(".") for paragraph in paragraphs) == sentences


def test_build_response_text_varies_by_index():
    assert build_response_text("short", index=0) == build_response_text("short", index=0)
    assert build_response_text("short", index=0) != build_response_text("short", index=1)


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        pytest.param([{"role": "user", "content": "hi"}], "hi", id="plain-string"),
        pytest.param(
            [{"role": "user", "content": [{"type": "input_text", "text": "hi"}, {"type": "image", "url": "x"}]}],
            "hi",
            id="content-parts",
        ),
        pytest.param(
            [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}],
            "first",
            id="skips-later-assistant-turn",
        ),
        pytest.param(
            [{"role": "user", "content": "first"}, {"role": "user", "content": "second"}],
            "second",
            id="most-recent-user-turn",
        ),
        pytest.param([{"role": "system", "content": "be nice"}], "", id="no-user-turn"),
    ],
)
def test_last_user_message(messages, expected):
    assert last_user_message(messages) == expected


def _post(payload: dict):
    return RequestFactory().post("/mock-llm/v1/", data=json.dumps(payload), content_type="application/json")


def _json(response):
    return json.loads(response.content)


def _sse_payloads(response) -> list[str]:
    """The `data:` lines of a streamed response, in order."""
    body = b"".join(response.streaming_content).decode()
    return [line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")]


def test_chat_completions():
    response = views.chat_completions(_post({"model": "stub", "messages": [{"role": "user", "content": "short"}]}))

    assert response.status_code == 200
    body = _json(response)
    assert body["object"] == "chat.completion"
    assert body["model"] == "stub"
    assert body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]


def test_chat_completions_without_messages_is_a_bad_request():
    response = views.chat_completions(_post({"model": "stub"}))

    assert response.status_code == 400
    assert _json(response)["error"]["code"] == "invalid_request_error"


def test_chat_completions_error_directive():
    response = views.chat_completions(_post({"messages": [{"role": "user", "content": "error insufficient_quota"}]}))

    assert response.status_code == 429
    assert _json(response)["error"]["code"] == "insufficient_quota"


def test_chat_completions_stream_joins_back_to_one_message():
    response = views.chat_completions(_post({"messages": [{"role": "user", "content": "short"}], "stream": True}))

    payloads = _sse_payloads(response)
    assert payloads[-1] == "[DONE]"
    chunks = [json.loads(payload) for payload in payloads[:-1]]
    assert {chunk["object"] for chunk in chunks} == {"chat.completion.chunk"}
    text = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk["choices"])
    assert text == build_response_text("short", index=0)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_chat_completions_stream_reports_usage_when_asked():
    def stream(stream_options):
        payload = {"messages": [{"role": "user", "content": "short"}], "stream": True}
        if stream_options is not None:
            payload["stream_options"] = stream_options
        chunks = [json.loads(line) for line in _sse_payloads(views.chat_completions(_post(payload)))[:-1]]
        return [chunk for chunk in chunks if "usage" in chunk]

    assert stream(None) == []
    assert stream({"include_usage": True})[0]["usage"]["total_tokens"] > 0


def test_responses():
    response = views.responses(_post({"model": "stub", "input": [{"role": "user", "content": "short"}]}))

    assert response.status_code == 200
    body = _json(response)
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"]
    assert body["usage"]["total_tokens"] == body["usage"]["input_tokens"] + body["usage"]["output_tokens"]


def test_responses_accepts_a_bare_string_input():
    response = views.responses(_post({"input": "short"}))

    assert response.status_code == 200
    assert _json(response)["output"][0]["content"][0]["text"]


def test_responses_without_input_is_a_bad_request():
    response = views.responses(_post({"model": "stub"}))

    assert response.status_code == 400
    assert _json(response)["error"]["code"] == "invalid_request_error"


def test_responses_stream():
    response = views.responses(_post({"input": "short", "stream": True}))

    events = [json.loads(payload) for payload in _sse_payloads(response)]
    assert events[0]["type"] == "response.created"
    assert events[-1]["type"] == "response.completed"
    assert [event["sequence_number"] for event in events] == list(range(len(events)))

    deltas = "".join(event["delta"] for event in events if event["type"] == "response.output_text.delta")
    assert deltas == events[-1]["response"]["output"][0]["content"][0]["text"]


def test_slow_directive_delays_the_reply(monkeypatch):
    slept = []
    monkeypatch.setattr(views.time, "sleep", slept.append)

    views.chat_completions(_post({"messages": [{"role": "user", "content": "slow 3"}]}))

    assert slept == [3.0]


def test_models_lists_the_stub_model():
    response = views.models(RequestFactory().get("/mock-llm/v1/models"))

    assert [model["id"] for model in _json(response)["data"]] == [MODEL_NAME]


@pytest.mark.parametrize(
    "view",
    [
        pytest.param(views.chat_completions, id="chat_completions"),
        pytest.param(views.responses, id="responses"),
        pytest.param(views.models, id="models"),
    ],
)
def test_views_are_not_served_outside_debug(view, settings):
    settings.DEBUG = False

    with pytest.raises(Http404):
        view(_post({}))


def _router_schema(*keywords) -> dict:
    """The shape OCS's router node sends: one field constrained to the configured keywords."""
    return {
        "type": "object",
        "properties": {"route": {"type": "string", "enum": list(keywords)}},
        "required": ["route"],
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("slow 100000", MAX_SLOW_SECONDS, id="clamped-to-the-maximum"),
        pytest.param("slow 10", 10.0, id="under-the-maximum-is-untouched"),
    ],
)
def test_slow_directive_is_clamped(text, expected):
    assert parse_directives(text).delay_seconds == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("error 999", id="above-the-http-range"),
        pytest.param("error 099", id="below-the-http-range"),
        pytest.param("error 200", id="success-status"),
        pytest.param("error 4291", id="too-many-digits"),
    ],
)
def test_error_directive_falls_back_on_an_unusable_status(text):
    """An unusable status must not reach Django, which refuses to serve one."""
    assert parse_directives(text).error == ErrorDirective(status=500, code="server_error")


@pytest.mark.parametrize(
    ("code", "expected_type"),
    [
        pytest.param("insufficient_quota", "insufficient_quota", id="quota"),
        pytest.param("rate_limit_exceeded", "rate_limit_error", id="rate-limit"),
        pytest.param("invalid_api_key", "authentication_error", id="auth"),
    ],
)
def test_error_body_reports_the_type_real_openai_uses(code, expected_type):
    assert error_body(ErrorDirective(status=429, code=code))["error"]["type"] == expected_type


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param({"type": "string", "enum": ["a", "b"]}, "a", id="first-enum-member"),
        pytest.param({"const": 7}, 7, id="const"),
        pytest.param({"type": "boolean"}, False, id="boolean"),
        pytest.param({"type": "null"}, None, id="null"),
        pytest.param({"type": "integer", "minimum": 5}, 5, id="integer-honours-minimum"),
        pytest.param({"type": "number"}, 0.0, id="number-default"),
        pytest.param({"type": ["string", "null"]}, "", id="nullable-picks-the-real-type"),
        pytest.param({"type": "array", "items": {"const": "x"}, "minItems": 2}, ["x", "x"], id="array-min-items"),
        pytest.param({"type": "array", "items": {"const": "x"}}, ["x"], id="array-defaults-to-one-item"),
        pytest.param({"anyOf": [{"const": 1}, {"const": 2}]}, 1, id="any-of-takes-the-first"),
        pytest.param({"oneOf": [{"const": 1}, {"const": 2}]}, 1, id="one-of-takes-the-first"),
    ],
)
def test_schema_sample(schema, expected):
    value = schemas.sample(schema)
    if expected == "":
        assert isinstance(value, str)
    else:
        assert value == expected


def test_schema_sample_fills_every_declared_property():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    value = schemas.sample(schema)

    assert isinstance(value, dict)
    assert set(value) == {"name", "age"}
    assert isinstance(value["name"], str)
    assert value["name"]
    assert isinstance(value["age"], int)


def test_schema_sample_follows_a_ref():
    """Pydantic emits a $ref plus $defs for a nested model."""
    schema = {
        "type": "object",
        "properties": {"child": {"$ref": "#/$defs/Child"}},
        "$defs": {"Child": {"type": "object", "properties": {"label": {"const": "leaf"}}}},
    }

    assert schemas.sample(schema) == {"child": {"label": "leaf"}}


def test_schema_sample_survives_a_self_referencing_ref():
    schema = {
        "type": "object",
        "properties": {"next": {"$ref": "#/$defs/Node"}},
        "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
    }

    assert schemas.sample(schema) is not None


def test_schema_sample_merges_all_of():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"a": {"const": 1}}},
            {"type": "object", "properties": {"b": {"const": 2}}},
        ]
    }

    assert schemas.sample(schema) == {"a": 1, "b": 2}


def test_schema_sample_respects_string_length_bounds():
    value = schemas.sample({"type": "string", "minLength": 40, "maxLength": 45})

    assert isinstance(value, str)
    assert 40 <= len(value) <= 45


def _chat_tools(schema: dict) -> list[dict]:
    return [{"type": "function", "function": {"name": "RouterOutput", "parameters": schema}}]


def _responses_tools(schema: dict) -> list[dict]:
    return [{"type": "function", "name": "RouterOutput", "parameters": schema}]


def test_chat_completions_calls_a_forced_tool():
    response = views.chat_completions(
        _post(
            {
                "messages": [{"role": "user", "content": "route me"}],
                "tools": _chat_tools(_router_schema("support", "sales")),
                "tool_choice": "required",
            }
        )
    )

    choice = _json(response)["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "RouterOutput"
    assert json.loads(call["function"]["arguments"]) == {"route": "support"}


def test_responses_calls_a_forced_tool():
    response = views.responses(
        _post(
            {
                "input": "route me",
                "tools": _responses_tools(_router_schema("support", "sales")),
                "tool_choice": "required",
            }
        )
    )

    item = _json(response)["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "RouterOutput"
    assert json.loads(item["arguments"]) == {"route": "support"}


@pytest.mark.parametrize(
    ("tool_choice", "expects_call"),
    [
        pytest.param("required", True, id="required"),
        pytest.param({"type": "function", "function": {"name": "RouterOutput"}}, True, id="named"),
        pytest.param("auto", False, id="auto-answers-with-text"),
        pytest.param(None, False, id="unset-answers-with-text"),
    ],
)
def test_a_tool_is_called_only_when_the_request_forces_one(tool_choice, expects_call):
    payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": _chat_tools(_router_schema("support")),
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    message = _json(views.chat_completions(_post(payload)))["choices"][0]["message"]

    assert bool(message.get("tool_calls")) is expects_call


def test_a_named_tool_choice_picks_that_tool():
    tools = [
        {"type": "function", "function": {"name": "first", "parameters": _router_schema("a")}},
        {"type": "function", "function": {"name": "second", "parameters": _router_schema("b")}},
    ]
    response = views.chat_completions(
        _post(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": tools,
                "tool_choice": {"type": "function", "function": {"name": "second"}},
            }
        )
    )

    call = _json(response)["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "second"
    assert json.loads(call["function"]["arguments"]) == {"route": "b"}


def test_chat_completions_answers_structured_output_with_conforming_json():
    response = views.chat_completions(
        _post(
            {
                "messages": [{"role": "user", "content": "classify"}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "Verdict", "schema": _router_schema("yes", "no")},
                },
            }
        )
    )

    content = _json(response)["choices"][0]["message"]["content"]
    assert json.loads(content) == {"route": "yes"}


def test_responses_answers_structured_output_with_conforming_json():
    response = views.responses(
        _post(
            {
                "input": "classify",
                "text": {"format": {"type": "json_schema", "name": "Verdict", "schema": _router_schema("yes", "no")}},
            }
        )
    )

    text = _json(response)["output"][0]["content"][0]["text"]
    assert json.loads(text) == {"route": "yes"}


def test_chat_completions_stream_rejoins_tool_call_arguments():
    response = views.chat_completions(
        _post(
            {
                "messages": [{"role": "user", "content": "route me"}],
                "tools": _chat_tools(_router_schema("support", "sales")),
                "tool_choice": "required",
                "stream": True,
            }
        )
    )

    chunks = [json.loads(payload) for payload in _sse_payloads(response)[:-1]]
    deltas = [chunk["choices"][0]["delta"] for chunk in chunks if chunk["choices"]]
    names = [call["function"]["name"] for delta in deltas for call in delta.get("tool_calls", []) if "id" in call]
    arguments = "".join(
        call["function"].get("arguments", "")
        for delta in deltas
        for call in delta.get("tool_calls", [])
        if "id" not in call
    )
    assert names == ["RouterOutput"]
    assert json.loads(arguments) == {"route": "support"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_responses_stream_rejoins_tool_call_arguments():
    response = views.responses(
        _post(
            {
                "input": "route me",
                "tools": _responses_tools(_router_schema("support", "sales")),
                "tool_choice": "required",
                "stream": True,
            }
        )
    )

    events = [json.loads(payload) for payload in _sse_payloads(response)]
    arguments = "".join(event["delta"] for event in events if event["type"] == "response.function_call_arguments.delta")
    assert json.loads(arguments) == {"route": "support"}
    assert events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["output"][0]["arguments"] == arguments


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"messages": [{"role": "user", "content": "short"}]}, id="text"),
        pytest.param(
            {
                "messages": [{"role": "user", "content": "route"}],
                "tools": _chat_tools(_router_schema("support")),
                "tool_choice": "required",
            },
            id="tool-call",
        ),
    ],
)
def test_chat_completion_envelope_validates_against_the_openai_sdk(payload):
    """Pins the wire format, so an `openai` upgrade that adds a required field fails here."""
    ChatCompletion.model_validate(_json(views.chat_completions(_post(payload))))


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"input": "short"}, id="text"),
        pytest.param(
            {
                "input": "route",
                "tools": _responses_tools(_router_schema("support")),
                "tool_choice": "required",
            },
            id="tool-call",
        ),
    ],
)
def test_response_envelope_validates_against_the_openai_sdk(payload):
    Response.model_validate(_json(views.responses(_post(payload))))


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"messages": [{"role": "user", "content": "short"}], "stream": True}, id="text"),
        pytest.param(
            {
                "messages": [{"role": "user", "content": "route"}],
                "tools": _chat_tools(_router_schema("support")),
                "tool_choice": "required",
                "stream": True,
            },
            id="tool-call",
        ),
        pytest.param(
            {
                "messages": [{"role": "user", "content": "short"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            id="with-usage",
        ),
    ],
)
def test_chat_completion_chunks_validate_against_the_openai_sdk(payload):
    for line in _sse_payloads(views.chat_completions(_post(payload)))[:-1]:
        ChatCompletionChunk.model_validate(json.loads(line))


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"input": "short", "stream": True}, id="text"),
        pytest.param(
            {
                "input": "route",
                "tools": _responses_tools(_router_schema("support")),
                "tool_choice": "required",
                "stream": True,
            },
            id="tool-call",
        ),
    ],
)
def test_response_stream_events_validate_against_the_openai_sdk(payload):
    adapter = TypeAdapter(ResponseStreamEvent)
    for line in _sse_payloads(views.responses(_post(payload))):
        adapter.validate_python(json.loads(line))


@pytest.fixture()
def urlconf_for_debug():
    """Re-import the root URLconf under a chosen DEBUG.

    `config.urls` decides whether to mount the mock at import time, and Django forces
    DEBUG off for the test run, so the mounting itself can only be exercised by importing
    the module again.
    """
    import config.urls  # noqa: PLC0415 - reloading this module is what the test exercises

    def _load(debug: bool):
        with override_settings(DEBUG=debug):
            importlib.reload(config.urls)
        clear_url_caches()

    yield _load
    importlib.reload(config.urls)
    clear_url_caches()


@pytest.mark.parametrize(
    ("path", "view"),
    [
        pytest.param("/mock-llm/v1/models", "models", id="models"),
        pytest.param("/mock-llm/v1/responses", "responses", id="responses"),
        pytest.param("/mock-llm/v1/chat/completions", "chat_completions", id="chat_completions"),
    ],
)
def test_urls_are_mounted_only_while_debug_is_on(urlconf_for_debug, path, view):
    urlconf_for_debug(True)
    resolved = resolve(path)
    assert resolved.func.__module__ == views.__name__
    assert resolved.func.__name__ == view

    urlconf_for_debug(False)
    with pytest.raises(Resolver404):
        resolve(path)
