from uuid import uuid4

import pytest

from apps.service_providers.tracing.ocs_tracer import OCSTracer
from apps.trace.models import Span, Trace
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestOCSTracer:
    def test_ending_trace_creates_trace_object(self, experiment):
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        tracer.end_trace()
        # The trace was never started, so no Trace object should be created
        assert Trace.objects.count() == 0

        tracer.start_trace(
            trace_name="test_trace",
            trace_id=uuid4(),
            session=session,
        )

        tracer.end_trace()
        assert Trace.objects.count() == 1

    def test_noop(self, experiment):
        """Starting or ending spans should do nothing if the tracer is not ready"""

        tracer = OCSTracer(experiment.id, experiment.team_id)

        tracer.start_span(
            span_id=uuid4(),
            span_name="test_span",
            inputs={"input": "data"},
            metadata={"meta": "data"},
        )

        tracer.end_span(span_id=uuid4(), outputs={}, error=None)

        tracer.end_trace(outputs={}, error=None)

    @pytest.mark.skip("spans disabled")
    def test_span_creation(self, experiment):
        """
        A span that is started should be added to the current trace. If there is an active span, it should be added
        to the trace with its parent span set to the current span.
        """
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        trace_id = uuid4()
        tracer.start_trace(
            trace_name="test_trace",
            trace_id=trace_id,
            session=session,
        )

        span_id = uuid4()
        tracer.start_span(
            span_id=span_id,
            span_name="test_span",
            inputs={"input": "data"},
            metadata={"meta": "data"},
        )
        trace = Trace.objects.get(trace_id=trace_id)
        span = Span.objects.get(trace__trace_id=trace_id)
        assert trace.spans.count() == 1
        assert span.trace == trace, "The span's trace is expected to be the same as the tracer's"
        assert span.parent_span is None, "The root span should not have a parent span"

        nested_span_id = uuid4()
        tracer.start_span(
            span_id=nested_span_id,
            span_name="child_span",
            inputs={"input": "data"},
            metadata={"meta": "data"},
        )
        assert trace.spans.count() == 2
        child_span = span.child_spans.first()
        assert child_span is not None, "Expected a child span, but got None"
        assert child_span.trace_id == span.trace_id, "The child span's trace is expected to be the same as the parent's"

        tracer.end_trace()

    @pytest.mark.skip("spans disabled")
    def test_span_with_error(self, experiment):
        """
        Test that a span with an error is properly recorded.
        """
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        trace_id = uuid4()
        tracer.start_trace(
            trace_name="test_trace",
            trace_id=trace_id,
            session=session,
        )

        span_id = uuid4()
        tracer.start_span(
            span_id=span_id,
            span_name="test_span",
            inputs={"input": "data"},
            metadata={"meta": "data"},
        )
        tracer.end_span(
            span_id=span_id,
            error=Exception("Test error"),
        )
        span = Span.objects.get(trace=tracer.trace)
        assert span.error is not None

    def test_record_experiment_version(self, experiment):
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        tracer.start_trace(
            trace_name="test_trace",
            trace_id=uuid4(),
            session=session,
        )

        assert experiment.working_version is None
        assert tracer.trace.experiment_version_number is None
        assert tracer.trace.experiment_id == experiment.id

        version = experiment.create_new_version()
        tracer = OCSTracer(version.id, experiment.team_id)
        tracer.start_trace(
            trace_name="test_trace",
            trace_id=uuid4(),
            session=session,
        )

        assert tracer.trace.experiment_version_number == version.version_number
        assert tracer.trace.experiment_id == experiment.id
