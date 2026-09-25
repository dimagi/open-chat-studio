"""Checks of ClientManager and LangFuseTracer against a real Langfuse SDK and OpenTelemetry.

Spans go to a recording exporter instead of the network, so the tests assert on what would
have been sent to Langfuse and on the threads left running, rather than on calls to a mock.
"""

import threading
import uuid
from unittest import mock

import pytest
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.langfuse import ClientManager, LangFuseTracer

# Nonzero, so it reaches the SDK instead of being short-circuited by `normalize_sample_rate`,
# but low enough that a sampled trace would take ~10^9 attempts.
NEAR_ZERO_RATE = 1e-9


class RecordingExporter(SpanExporter):
    def __init__(self):
        self.span_names = []

    def export(self, spans):
        self.span_names.extend(span.name for span in spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        # A retired client shuts down its span processor, which shuts down this exporter.
        # The test's exporter has to keep recording for the client built after it.
        pass


@pytest.fixture()
def exporter():
    return RecordingExporter()


@pytest.fixture()
def config(exporter):
    """A trace provider config with a key unique to this test.

    The SDK caches one resource manager per public_key process-wide, so a shared key would
    hand one test another test's client.
    """
    return {
        "public_key": f"pk-lifecycle-{uuid.uuid4().hex}",
        "secret_key": "sk-lifecycle-test",
        "base_url": "http://localhost:1",
        "span_exporter": exporter,
    }


@pytest.fixture()
def manager():
    manager = ClientManager(prune_interval=3600)
    with mock.patch("apps.service_providers.tracing.langfuse.client_manager", manager):
        yield manager
    manager.shutdown()


@pytest.fixture()
def mock_session():
    session = mock.MagicMock()
    session.external_id = "ext-session-id"
    session.participant = mock.MagicMock(identifier="participant-1")
    return session


def _prune_all(manager):
    for entry in manager._entries.values():
        entry.last_used -= manager.stale_timeout + 1
    manager._prune_stale()


def test_a_span_is_exported_once_after_its_client_is_rebuilt(manager, config, exporter):
    """Each rebuild used to add a span processor that was never removed, and every processor
    for the key exported every span.
    """
    manager.get(config)
    _prune_all(manager)
    client = manager.get(config)

    with client.start_as_current_observation(name="probe"):
        pass
    client.flush()

    assert exporter.span_names.count("probe") == 1


def test_pruning_a_client_stops_every_thread_it_started(manager, config):
    threads_before = set(threading.enumerate())
    manager.get(config)

    _prune_all(manager)

    leaked = [thread.name for thread in threading.enumerate() if thread not in threads_before]
    assert leaked == []


@pytest.mark.usefixtures("manager")
@pytest.mark.parametrize(
    ("first_rate", "second_rate", "expected_traced"),
    [
        pytest.param(NEAR_ZERO_RATE, 1.0, 10, id="full-rate-chatbot-after-low-rate-chatbot"),
        pytest.param(1.0, NEAR_ZERO_RATE, 0, id="low-rate-chatbot-after-full-rate-chatbot"),
    ],
)
def test_each_chatbot_is_sampled_at_its_own_rate(
    config, exporter, mock_session, first_rate, second_rate, expected_traced
):
    """Two chatbots share a trace provider (one public_key) but set different sample rates.
    The chatbot whose client was built first used to fix the rate for both.
    """
    first_chatbot = LangFuseTracer("langfuse", {**config, "sample_rate": first_rate})
    second_chatbot = LangFuseTracer("langfuse", {**config, "sample_rate": second_rate})

    with first_chatbot.trace(TraceContext(id=1, name="first-chatbot"), session=mock_session):
        pass
    for i in range(10):
        with second_chatbot.trace(TraceContext(id=i, name="second-chatbot"), session=mock_session):
            pass

    assert exporter.span_names.count("second-chatbot") == expected_traced


def _prune_by_max_clients(manager, config):
    manager.max_clients = 1
    manager.get({**config, "public_key": f"pk-lifecycle-{uuid.uuid4().hex}"})
    manager._prune_stale()


def _rotate_secret(manager, config):
    manager.get({**config, "secret_key": "sk-lifecycle-rotated"})


@pytest.mark.parametrize(
    "evict",
    [
        pytest.param(lambda manager, _config: _prune_all(manager), id="stale-prune"),
        pytest.param(_prune_by_max_clients, id="max-clients-prune"),
        pytest.param(_rotate_secret, id="secret-rotation"),
    ],
)
def test_a_trace_is_exported_when_its_client_is_evicted_mid_trace(manager, config, exporter, mock_session, evict):
    tracer = LangFuseTracer("langfuse", config)

    with tracer.trace(TraceContext(id=1, name="in-flight"), session=mock_session):
        evict(manager, config)
        with tracer.span(TraceContext(id=2, name="after-eviction"), inputs={}):
            pass

    assert exporter.span_names.count("in-flight") == 1
    assert exporter.span_names.count("after-eviction") == 1


def test_a_client_retired_mid_trace_is_shut_down_when_the_trace_ends(manager, config, mock_session):
    threads_before = set(threading.enumerate())
    tracer = LangFuseTracer("langfuse", config)

    with tracer.trace(TraceContext(id=1, name="in-flight"), session=mock_session):
        _rotate_secret(manager, config)
    manager.shutdown()

    leaked = [thread.name for thread in threading.enumerate() if thread not in threads_before]
    assert leaked == []
