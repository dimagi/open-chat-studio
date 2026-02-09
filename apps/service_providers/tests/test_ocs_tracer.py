from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from apps.ocs_notifications.models import LevelChoices
from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.ocs_tracer import OCSCallbackHandler, OCSTracer
from apps.trace.models import Span, Trace
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestOCSTracer:
    def test_ending_trace_creates_trace_object(self, experiment):
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        # Initially no traces exist
        assert Trace.objects.count() == 0

        # Using the context manager creates a trace
        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            pass

        assert Trace.objects.count() == 1

    def test_noop(self, experiment):
        """Span context manager should do nothing if the tracer is not ready"""

        tracer = OCSTracer(experiment.id, experiment.team_id)

        # Using span context manager when tracer is not ready should not raise an error
        span_context = TraceContext(id=uuid4(), name="test_span")
        with tracer.span(
            span_context=span_context,
            inputs={"input": "data"},
            metadata={"meta": "data"},
        ):
            # Should execute without errors even though tracer is not ready
            pass

        # Verify no Span objects were created
        assert Span.objects.count() == 0

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
        span = Span.objects.get(trace=tracer.trace_record)
        assert span.error is not None

    def test_record_experiment_version(self, experiment):
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            assert experiment.working_version is None
            assert tracer.trace_record.experiment_version_number is None
            assert tracer.trace_record.experiment_id == experiment.id

        version = experiment.create_new_version()
        tracer = OCSTracer(version.id, experiment.team_id)
        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            assert tracer.trace_record.experiment_version_number == version.version_number
            assert tracer.trace_record.experiment_id == experiment.id

    def test_trace_error_recording(self, experiment):
        """Test that errors during trace execution are captured in the trace record"""
        tracer = OCSTracer(experiment.id, experiment.team_id)
        session = ExperimentSessionFactory()

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        error_message = "Test error message"

        # Test that exception is raised and captured
        with pytest.raises(ValueError, match=error_message):
            with tracer.trace(trace_context=trace_context, session=session):
                raise ValueError(error_message)

        # Verify the trace was created with error status and message
        trace = Trace.objects.get(trace_id=trace_context.id)
        assert trace.status == "error"
        assert trace.error == error_message


class TestOCSCallbackHandler:
    @patch("apps.service_providers.tracing.ocs_tracer.Team.objects.get")
    @patch("apps.service_providers.tracing.ocs_tracer.create_notification")
    def test_on_llm_error_creates_notification(self, mock_create_notification, mock_get_team):
        """Test that LLM error handler creates a notification."""
        # Set up team mock
        team = Mock()
        team.id = 123
        mock_get_team.return_value = team

        # Set up tracer
        tracer = OCSTracer(experiment_id=456, team_id=team.id)
        tracer.trace_id = str(uuid4())

        # Set up a session with a participant so the notification includes context
        participant = Mock()
        participant.identifier = "user@example.com"
        session = Mock()
        session.participant = participant
        tracer.session = session

        # Create callback handler and mock _get_bot_name to avoid DB hit
        callback_handler = OCSCallbackHandler(tracer=tracer)
        callback_handler._get_bot_name = Mock(return_value="My Bot")

        # Trigger LLM error
        error_message = "Connection timeout to OpenAI API"
        callback_handler.on_llm_error(error=Exception(error_message))

        # Verify Team.objects.get was called with correct team_id
        mock_get_team.assert_called_once_with(id=team.id)

        # Verify notification was created with correct parameters
        mock_create_notification.assert_called_once()
        call_kwargs = mock_create_notification.call_args[1]
        assert call_kwargs["title"] == "LLM Error Detected"
        assert call_kwargs["message"] == (
            f"An LLM error occurred for bot 'My Bot' and participant 'user@example.com': {error_message}"
        )
        assert call_kwargs["level"] == LevelChoices.ERROR
        assert call_kwargs["team"] == team
        assert call_kwargs["slug"] == "llm-error"
        assert call_kwargs["event_data"] == {"bot_id": 456, "error_message": error_message}

        # Verify tracer state is updated
        assert tracer.error_detected is True
        assert tracer.error_message == error_message
