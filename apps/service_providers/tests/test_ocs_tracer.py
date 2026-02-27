from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing.base import SpanNotificationConfig, TraceContext
from apps.service_providers.tracing.ocs_tracer import OCSCallbackHandler, OCSTracer
from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
class TestOCSTracer:
    def test_ending_trace_creates_trace_object(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]

        # Initially no traces exist
        assert Trace.objects.count() == 0

        # Using the context manager creates a trace
        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            pass

        assert Trace.objects.count() == 1

    def test_noop(self, experiment):
        """Span context manager should do nothing if the tracer is not ready"""

        tracer = OCSTracer(experiment, experiment.team_id)

        # Using span context manager when tracer is not ready should not raise an error
        span_context = TraceContext(id=uuid4(), name="test_span")
        with tracer.span(
            span_context=span_context,
            inputs={"input": "data"},
            metadata={"meta": "data"},
        ):
            # Should execute without errors even though tracer is not ready
            pass

    def test_record_experiment_version(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            assert experiment.working_version is None
            assert tracer.trace_record.experiment_version_number is None
            assert tracer.trace_record.experiment_id == experiment.id

        version = experiment.create_new_version()
        tracer = OCSTracer(version, experiment.team_id)
        trace_context = TraceContext(id=uuid4(), name="test_trace")
        with tracer.trace(trace_context=trace_context, session=session):
            assert tracer.trace_record.experiment_version_number == version.version_number
            assert tracer.trace_record.experiment_id == experiment.id

    def test_trace_error_recording(self, experiment):
        """Test that errors during trace execution are captured in the trace record"""
        tracer = OCSTracer(experiment, experiment.team_id)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]

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
    def test_on_llm_error_records_error(self):
        """LLM error handler records the error on the tracer."""
        experiment = Mock(id=456)
        tracer = OCSTracer(experiment, team_id=123)
        tracer.trace_id = str(uuid4())  # ty: ignore[invalid-assignment]
        tracer.session = Mock()

        callback_handler = OCSCallbackHandler(tracer=tracer)

        error_message = "Connection timeout to OpenAI API"
        callback_handler.on_llm_error(error=Exception(error_message))

        assert tracer.error_detected is True
        assert tracer.error_message == error_message


@pytest.mark.django_db()
class TestOCSTracerNotifications:
    def _make_tracer(self, experiment):
        return OCSTracer(experiment, experiment.team_id)

    def _run_trace_with_span_error(self, tracer, session, trace_context, span_context, error_msg="boom"):
        """Helper to run a trace+span pair that raises, keeping pytest.raises to one statement."""
        with tracer.trace(trace_context=trace_context, session=session):
            with tracer.span(span_context=span_context, inputs={}):
                raise ValueError(error_msg)

    def test_span_with_notification_config_is_captured_on_error(self, experiment):
        """When a span whose TraceContext carries notification_config raises, OCSTracer records it."""
        # Use a published (non-working) version so notification firing is allowed
        published = experiment.create_new_version()
        tracer = self._make_tracer(published)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]
        config = SpanNotificationConfig(permissions=["experiments.change_experiment"])

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        # Manually set notification_config on span_context, as TracingService.span() would
        span_context = TraceContext(id=uuid4(), name="Run Pipeline", notification_config=config)

        fired_name = []
        fired_config = []

        def capture_fire(self_):
            fired_name.append(self_.error_span_name)
            fired_config.append(self_.error_notification_config)

        with patch.object(OCSTracer, "_fire_trace_error_notification", capture_fire):
            with pytest.raises(ValueError, match="boom"):
                self._run_trace_with_span_error(tracer, session, trace_context, span_context)

        assert fired_name == ["Run Pipeline"]
        assert fired_config == [config]

    def test_only_innermost_erroring_span_is_captured(self, experiment):
        """When nested spans both exit with an error, only the innermost span's config wins."""
        # Use a published (non-working) version so the working-version guard doesn't hide the failure
        published = experiment.create_new_version()
        tracer = self._make_tracer(published)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]
        inner_config = SpanNotificationConfig(permissions=["experiments.change_experiment"])
        outer_config = SpanNotificationConfig(permissions=["experiments.view_experiment"])

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        outer_context = TraceContext(id=uuid4(), name="Process Message", notification_config=outer_config)
        inner_context = TraceContext(id=uuid4(), name="Run Pipeline", notification_config=inner_config)

        fired_name = []

        def capture_fire(self_):
            fired_name.append(self_.error_span_name)

        def run_nested():
            with tracer.trace(trace_context=trace_context, session=session):
                with tracer.span(span_context=outer_context, inputs={}):
                    with tracer.span(span_context=inner_context, inputs={}):
                        raise ValueError("nested boom")

        with patch.object(OCSTracer, "_fire_trace_error_notification", capture_fire):
            with pytest.raises(ValueError, match="nested boom"):
                run_nested()

        # Innermost span ("Run Pipeline") wins — it exits first
        assert fired_name == ["Run Pipeline"]

    def test_notification_not_fired_for_working_version(self, experiment):
        """Notification is NOT fired when the experiment is a working version."""
        # The base experiment fixture is always the working version
        assert experiment.is_working_version
        tracer = self._make_tracer(experiment)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]
        config = SpanNotificationConfig(permissions=["experiments.change_experiment"])

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        span_context = TraceContext(id=uuid4(), name="Run Pipeline", notification_config=config)

        with patch.object(OCSTracer, "_fire_trace_error_notification") as mock_fire:
            with pytest.raises(ValueError, match="boom"):
                self._run_trace_with_span_error(tracer, session, trace_context, span_context)

        mock_fire.assert_not_called()

    def test_notification_not_fired_when_span_has_no_config(self, experiment):
        """Notification is NOT fired when the erroring span had no notification_config."""
        # Use a published (non-working) version so the working-version guard doesn't hide the failure
        published = experiment.create_new_version()
        tracer = self._make_tracer(published)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        # No notification_config on span_context
        span_context = TraceContext(id=uuid4(), name="Run Pipeline")

        with patch.object(OCSTracer, "_fire_trace_error_notification") as mock_fire:
            with pytest.raises(ValueError, match="boom"):
                self._run_trace_with_span_error(tracer, session, trace_context, span_context)

        mock_fire.assert_not_called()

    def test_state_is_reset_after_trace_exits(self, experiment):
        """error_span_name and error_notification_config are reset after trace exits."""
        # The base experiment fixture is the working version — notification won't fire
        assert experiment.is_working_version
        tracer = self._make_tracer(experiment)
        session: ExperimentSession = ExperimentSessionFactory()  # ty: ignore[invalid-assignment]
        config = SpanNotificationConfig(permissions=["experiments.change_experiment"])

        trace_context = TraceContext(id=uuid4(), name="test_trace")
        span_context = TraceContext(id=uuid4(), name="Run Pipeline", notification_config=config)

        with pytest.raises(ValueError, match="boom"):
            self._run_trace_with_span_error(tracer, session, trace_context, span_context)

        assert tracer.error_span_name == ""
        assert tracer.error_notification_config is None
        assert tracer.error_detected is False
        assert tracer.error_message == ""
