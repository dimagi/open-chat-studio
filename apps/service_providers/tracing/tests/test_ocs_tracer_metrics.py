from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.outputs import LLMResult

from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.service_providers.tracing.ocs_tracer import OCSCallbackHandler, OCSTracer
from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestOCSTracerMetrics:
    def test_metrics_collector_created_during_trace(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create()

        assert tracer.metrics_collector is None

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            assert tracer.metrics_collector is not None
            assert isinstance(tracer.metrics_collector, MetricsCollector)

        # Reset after trace exits
        assert tracer.metrics_collector is None

    def test_metrics_persisted_to_trace_record(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create()

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            collector = tracer.metrics_collector
            # Simulate LLM calls
            collector.on_llm_start({}, ["prompt 1"])
            collector.on_llm_end(
                LLMResult(
                    generations=[],
                    llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
                )
            )
            collector.on_tool_start({"name": "search"}, "query")

        trace = Trace.objects.get(trace_id=trace_context.id)
        assert trace.n_turns == 1
        assert trace.n_toolcalls == 1
        assert trace.n_total_tokens == 150

    def test_no_llm_calls_metrics_null(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create()

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            # No LLM calls during trace
            pass

        trace = Trace.objects.get(trace_id=trace_context.id)
        assert trace.n_turns is None
        assert trace.n_toolcalls is None
        assert trace.n_total_tokens is None
        assert trace.time_to_first_token is None
        assert trace.time_to_last_token is None

    def test_metrics_persisted_on_error(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create()
        trace_context = TraceContext(id=uuid4(), name="test_trace")

        def _run_trace_with_metrics_then_error():
            with tracer.trace(trace_context=trace_context, session=session):
                tracer.metrics_collector.on_llm_start({}, ["prompt"])
                tracer.metrics_collector.on_llm_end(
                    LLMResult(
                        generations=[],
                        llm_output={"token_usage": {"prompt_tokens": 50, "completion_tokens": 20}},
                    )
                )
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _run_trace_with_metrics_then_error()

        trace = Trace.objects.get(trace_id=trace_context.id)
        assert trace.status == "error"
        assert trace.n_turns == 1
        assert trace.n_total_tokens == 70


class TestOCSCallbackHandlerMetricsDelegation:
    def test_on_llm_start_delegates_to_collector(self):
        tracer = OCSTracer(Mock(id=1), team_id=1)
        tracer.metrics_collector = MetricsCollector(start_time=0.0)

        handler = OCSCallbackHandler(tracer=tracer)
        handler.on_llm_start({}, ["prompt"])

        assert tracer.metrics_collector._turns == 1

    def test_on_llm_new_token_delegates_to_collector(self):
        tracer = OCSTracer(Mock(id=1), team_id=1)
        tracer.metrics_collector = MetricsCollector(start_time=0.0)

        handler = OCSCallbackHandler(tracer=tracer)
        handler.on_llm_new_token("Hello")

        assert tracer.metrics_collector._first_token_recorded is True

    def test_on_llm_end_delegates_to_collector(self):
        tracer = OCSTracer(Mock(id=1), team_id=1)
        tracer.metrics_collector = MetricsCollector(start_time=0.0)

        handler = OCSCallbackHandler(tracer=tracer)
        handler.on_llm_end(
            LLMResult(
                generations=[],
                llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            )
        )

        assert tracer.metrics_collector._total_tokens == 15

    def test_on_tool_start_delegates_to_collector(self):
        tracer = OCSTracer(Mock(id=1), team_id=1)
        tracer.metrics_collector = MetricsCollector(start_time=0.0)

        handler = OCSCallbackHandler(tracer=tracer)
        handler.on_tool_start({"name": "tool"}, "input")

        assert tracer.metrics_collector._toolcalls == 1

    def test_no_collector_does_not_error(self):
        """When metrics_collector is None, callback methods should be no-ops."""
        tracer = OCSTracer(Mock(id=1), team_id=1)
        assert tracer.metrics_collector is None

        handler = OCSCallbackHandler(tracer=tracer)
        # None of these should raise
        handler.on_llm_start({}, ["prompt"])
        handler.on_llm_new_token("token")
        handler.on_llm_end(LLMResult(generations=[]))
        handler.on_tool_start({}, "input")

    def test_error_capture_still_works(self):
        """Metrics delegation does not break existing error capture."""
        tracer = OCSTracer(Mock(id=1), team_id=1)
        handler = OCSCallbackHandler(tracer=tracer)

        handler.on_llm_error(error=Exception("timeout"))
        assert tracer.error_detected is True
        assert tracer.error_message == "timeout"
