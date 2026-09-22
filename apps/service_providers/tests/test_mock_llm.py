import json

import pytest
from django.http import Http404
from django.test import RequestFactory

from apps.service_providers.mock_llm import views
from apps.service_providers.mock_llm.directives import (
    DEFAULT_SLOW_SECONDS,
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
