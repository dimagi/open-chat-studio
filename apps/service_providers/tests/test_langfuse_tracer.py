from contextlib import contextmanager
from unittest import mock

import pytest

from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.langfuse import LangFuseTracer
from apps.service_providers.tracing.service import TracingService


@pytest.fixture()
def mock_langfuse_client():
    client = mock.MagicMock()
    span = mock.MagicMock()
    span.trace_id = "trace-abc-123"
    span.id = "span-abc-123"

    @contextmanager
    def start_observation(**kwargs):
        yield span

    client.start_as_current_observation.side_effect = start_observation
    client.get_current_trace_id.return_value = "trace-abc-123"
    client.get_trace_url.return_value = "https://langfuse.example/trace/trace-abc-123"
    # Stash the span mock so tests can introspect calls made on it.
    client._test_span = span
    return client


@pytest.fixture()
def mock_session():
    session = mock.MagicMock()
    session.external_id = "ext-session-id"
    session.participant = mock.MagicMock(identifier="participant-1")
    return session


@pytest.fixture()
def patched_tracer(mock_langfuse_client):
    """A real LangFuseTracer with the ClientManager boundary mocked."""
    tracer = LangFuseTracer("langfuse", {"public_key": "pk", "secret_key": "sk"})
    with mock.patch(
        "apps.service_providers.tracing.langfuse.client_manager.get",
        return_value=mock_langfuse_client,
    ):
        yield tracer


def test_get_trace_metadata_returns_langfuse_info(patched_tracer, mock_langfuse_client, mock_session):
    """get_trace_metadata() must return trace_id/trace_url while trace is active."""
    trace_context = TraceContext(id=mock.sentinel.trace_id, name="test-trace")

    with patched_tracer.trace(trace_context=trace_context, session=mock_session):
        metadata = patched_tracer.get_trace_metadata()

    assert metadata == {
        "trace_id": "trace-abc-123",
        "trace_url": "https://langfuse.example/trace/trace-abc-123",
        "trace_provider": "langfuse",
    }


def test_tracing_service_exposes_langfuse_metadata_via_trace_info(patched_tracer, mock_langfuse_client, mock_session):
    """TracingService.get_trace_metadata() wraps LangFuseTracer output under
    'trace_info', which is what gets merged into ChatMessage.metadata.
    """
    service = TracingService([patched_tracer], experiment_id=1, team_id=1)

    with service.trace("test-trace", session=mock_session):
        metadata = service.get_trace_metadata()

    assert metadata == {
        "trace_info": [
            {
                "trace_id": "trace-abc-123",
                "trace_url": "https://langfuse.example/trace/trace-abc-123",
                "trace_provider": "langfuse",
            }
        ]
    }


def test_tracing_service_recovers_when_trace_url_fetch_fails(patched_tracer, mock_langfuse_client, mock_session):
    """A URL-fetch failure must not drop the whole langfuse entry from trace_info."""
    mock_langfuse_client.get_trace_url.side_effect = ConnectionError("boom")
    service = TracingService([patched_tracer], experiment_id=1, team_id=1)

    with service.trace("test-trace", session=mock_session):
        metadata = service.get_trace_metadata()

    assert metadata.get("trace_info"), "langfuse trace info must survive URL fetch failure"
    entry = metadata["trace_info"][0]
    assert entry["trace_id"] == "trace-abc-123"
    assert entry["trace_provider"] == "langfuse"


def test_add_trace_tags_uses_resolved_trace_id(patched_tracer, mock_langfuse_client, mock_session):
    trace_context = TraceContext(id=mock.sentinel.trace_id, name="test-trace")

    with patched_tracer.trace(trace_context=trace_context, session=mock_session):
        patched_tracer.add_trace_tags(["tag1", "tag2"])

    mock_langfuse_client._create_trace_tags_via_ingestion.assert_called_once_with(
        trace_id="trace-abc-123", tags=["tag1", "tag2"]
    )


def test_trace_state_resets_on_exit(patched_tracer, mock_langfuse_client, mock_session):
    trace_context = TraceContext(id=mock.sentinel.trace_id, name="test-trace")

    with patched_tracer.trace(trace_context=trace_context, session=mock_session):
        assert patched_tracer.ready
        assert patched_tracer._langfuse_trace_id == "trace-abc-123"

    assert not patched_tracer.ready
    assert patched_tracer._langfuse_trace_id is None
    assert patched_tracer.trace_record is None
    assert patched_tracer.client is None
    assert patched_tracer.session is None


def test_span_marks_level_error_when_exception_propagates(patched_tracer, mock_langfuse_client, mock_session):
    """If user code under `with span(...)` raises, the span must surface as ERROR in Langfuse
    instead of looking like a successful span — otherwise failures are invisible in the trace UI.
    """
    trace_context = TraceContext(id=mock.sentinel.trace_id, name="test-trace")
    span_context = TraceContext(id=mock.sentinel.span_id, name="failing-span")

    with patched_tracer.trace(trace_context=trace_context, session=mock_session):
        with pytest.raises(RuntimeError, match="boom"):
            with patched_tracer.span(span_context=span_context, inputs={}):
                raise RuntimeError("boom")

    mock_langfuse_client._test_span.update.assert_any_call(level="ERROR", status_message=mock.ANY)
    assert span_context.exception is not None
    assert span_context.error is not None


def test_trace_marks_level_error_when_exception_propagates(patched_tracer, mock_langfuse_client, mock_session):
    """Same guarantee at the trace level: a propagating exception must mark the trace as ERROR."""
    trace_context = TraceContext(id=mock.sentinel.trace_id, name="test-trace")

    with pytest.raises(RuntimeError, match="boom"):
        with patched_tracer.trace(trace_context=trace_context, session=mock_session):
            raise RuntimeError("boom")

    mock_langfuse_client._test_span.update.assert_any_call(level="ERROR", status_message=mock.ANY)
    assert trace_context.exception is not None
