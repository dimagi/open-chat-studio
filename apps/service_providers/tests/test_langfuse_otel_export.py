"""End-to-end checks of what LangFuseTracer actually puts on the wire.

These run a real ``Langfuse`` client through the real OpenTelemetry pipeline with the
span exporter swapped for an in-memory one, so they assert on the exported spans rather
than on calls to a mock. That is the only way to confirm trace-level attributes land on
the observation Langfuse reads them from.
"""

import uuid
from unittest import mock

import pytest
from langfuse import Langfuse, LangfuseOtelSpanAttributes
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.langfuse import LangFuseTracer, _detach_sdk_resources
from apps.service_providers.tracing.service import TracingService


@pytest.fixture()
def public_key():
    """A key unique to this test.

    The SDK caches one resource manager per public_key process-wide, so a shared key would
    give every test the first test's exporter and, once one test had torn its client down,
    hang the next one's flush on dead consumer threads.
    """
    return f"pk-otel-{uuid.uuid4().hex}"


@pytest.fixture()
def exported_spans(public_key):
    """A real Langfuse client whose spans are collected in memory instead of sent.

    All three parts are torn down: the key is unregistered so the next test builds its
    own resource manager, the client releases its consumer threads, and the provider is shut
    down separately because the span processor's worker thread belongs to the provider.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    client = Langfuse(
        public_key=public_key,
        secret_key="sk-otel-test",
        base_url="http://localhost:1",
        tracer_provider=provider,
        span_exporter=exporter,
    )
    yield client, exporter
    _detach_sdk_resources(public_key)
    client.shutdown()
    provider.shutdown()


@pytest.fixture()
def tracer(public_key, exported_spans):
    client, _exporter = exported_spans
    tracer = LangFuseTracer("langfuse", {"public_key": public_key})
    with mock.patch("apps.service_providers.tracing.langfuse.client_manager.get", return_value=client):
        yield tracer


@pytest.fixture()
def mock_session():
    session = mock.MagicMock()
    session.external_id = "ext-session-id"
    session.participant = mock.MagicMock(identifier="participant-1")
    return session


def _span_by_name(exporter, name):
    spans = [span for span in exporter.get_finished_spans() if span.name == name]
    assert len(spans) == 1, f"expected exactly one {name!r} span, got {len(spans)}"
    return spans[0]


def test_trace_tags_are_exported_on_the_root_observation(tracer, exported_spans, mock_session):
    """Tags are added from a nested span but must end up on the root observation, which is
    where Langfuse reads trace-level attributes from.
    """
    _client, exporter = exported_spans

    with tracer.trace(trace_context=TraceContext(id=1, name="root-trace"), session=mock_session):
        with tracer.span(span_context=TraceContext(id=2, name="Run Pipeline"), inputs={}):
            tracer.add_trace_tags(["safety_layer:blocked"])

    root = _span_by_name(exporter, "root-trace")
    assert root.attributes[LangfuseOtelSpanAttributes.TRACE_TAGS] == ("safety_layer:blocked",)

    nested = _span_by_name(exporter, "Run Pipeline")
    assert LangfuseOtelSpanAttributes.TRACE_TAGS not in nested.attributes


def test_repeated_tag_calls_accumulate_instead_of_replacing(tracer, exported_spans, mock_session):
    """A trace can save more than one AI message, so the second call must not drop the tags
    the first one set.
    """
    _client, exporter = exported_spans

    with tracer.trace(trace_context=TraceContext(id=1, name="root-trace"), session=mock_session):
        tracer.add_trace_tags(["first"])
        tracer.add_trace_tags(["first", "second"])

    root = _span_by_name(exporter, "root-trace")
    assert root.attributes[LangfuseOtelSpanAttributes.TRACE_TAGS] == ("first", "second")


def test_tags_repeated_within_one_call_are_exported_once(tracer, exported_spans, mock_session):
    """Langfuse trace tags are a set of strings; a repeat carries no meaning."""
    _client, exporter = exported_spans

    with tracer.trace(trace_context=TraceContext(id=1, name="root-trace"), session=mock_session):
        tracer.add_trace_tags(["dup", "other", "dup"])

    root = _span_by_name(exporter, "root-trace")
    assert root.attributes[LangfuseOtelSpanAttributes.TRACE_TAGS] == ("dup", "other")


def test_tags_do_not_leak_between_traces(tracer, exported_spans, mock_session):
    _client, exporter = exported_spans

    with tracer.trace(trace_context=TraceContext(id=1, name="first-trace"), session=mock_session):
        tracer.add_trace_tags(["only-on-first"])

    with tracer.trace(trace_context=TraceContext(id=2, name="second-trace"), session=mock_session):
        pass

    assert LangfuseOtelSpanAttributes.TRACE_TAGS not in _span_by_name(exporter, "second-trace").attributes


def test_session_and_user_are_exported_on_every_observation(tracer, exported_spans, mock_session):
    """`propagate_attributes` is what binds a trace to a session; assert it survives the
    export rather than trusting the call was made.
    """
    _client, exporter = exported_spans

    with tracer.trace(trace_context=TraceContext(id=1, name="root-trace"), session=mock_session):
        with tracer.span(span_context=TraceContext(id=2, name="Run Pipeline"), inputs={}):
            pass

    for name in ("root-trace", "Run Pipeline"):
        span = _span_by_name(exporter, name)
        assert span.attributes[LangfuseOtelSpanAttributes.TRACE_SESSION_ID] == "ext-session-id"
        assert span.attributes[LangfuseOtelSpanAttributes.TRACE_USER_ID] == "participant-1"


def test_output_message_tags_reach_the_exported_trace_through_tracing_service(tracer, exported_spans, mock_session):
    """The production path: `bots.py` adds output message tags from inside the "Run Pipeline"
    span, and `TracingService` swallows tracer errors, so only the exported span shows
    whether the tags actually made it.
    """
    _client, exporter = exported_spans
    service = TracingService([tracer], experiment_id=1, team_id=1)

    with service.trace("chat-trace", session=mock_session):
        with service.span("Run Pipeline", inputs={}):
            service.add_output_message_tags_to_trace(["safety_layer:blocked"])

    root = _span_by_name(exporter, "chat-trace")
    assert root.attributes[LangfuseOtelSpanAttributes.TRACE_TAGS] == ("safety_layer:blocked",)
