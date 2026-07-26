"""Tests for ProcessingStage.__call__ span-status behavior.

Verifies that a stage's trace span honestly reflects what happened:
- genuine failures (raised or recorded on the context) mark the span as errored,
- control-flow signals (early exit / abort / cancellation) do not.
"""

import pytest

from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.stages.base import ProcessingStage
from apps.channels.stages.terminal import MessageDeliveryFailure
from apps.service_providers.llm_service.runnables import GenerationCancelled

from ..conftest import make_context, make_trace_service


class _RecordsSendingException(ProcessingStage):
    """Catches a delivery failure and records it instead of raising."""

    def process(self, ctx):
        ctx.sending_exceptions.append(MessageDeliveryFailure(ValueError("fcm 500"), context="text message"))


class _RecordsProcessingError(ProcessingStage):
    def process(self, ctx):
        ctx.processing_errors.append("something degraded")


class _RaisesSignal(ProcessingStage):
    def __init__(self, signal):
        self._signal = signal

    def process(self, ctx):
        raise self._signal


class _RaisesGenuineError(ProcessingStage):
    def process(self, ctx):
        raise RuntimeError("kaboom")


class _RecordsErrorThenEarlyExits(ProcessingStage):
    def process(self, ctx):
        ctx.processing_errors.append("fallback used")
        raise EarlyExitResponse("here is a fallback reply")


class _HappyStage(ProcessingStage):
    def process(self, ctx):
        pass


class TestHandledErrorsMarkSpan:
    def test_recorded_sending_exception_marks_span_as_error(self):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        _RecordsSendingException()(ctx)

        span.mark_span_as_error.assert_called_once()
        message, kwargs = span.mark_span_as_error.call_args
        assert "fcm 500" in message[0]
        # The underlying exception is passed so the span gets a real traceback.
        assert isinstance(kwargs["exception"], ValueError)

    def test_recorded_processing_error_marks_span_as_error(self):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        _RecordsProcessingError()(ctx)

        span.mark_span_as_error.assert_called_once()
        assert "something degraded" in span.mark_span_as_error.call_args[0][0]

    def test_handled_errors_surface_in_span_outputs(self):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        _RecordsProcessingError()(ctx)

        outputs = span.set_outputs.call_args[0][0]
        assert outputs["handled_errors"] == ["something degraded"]

    def test_only_newly_recorded_errors_are_attributed_to_the_stage(self):
        """Pre-existing errors from earlier stages don't re-flag this stage's span."""
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service, processing_errors=["earlier failure"])

        _HappyStage()(ctx)

        span.mark_span_as_error.assert_not_called()


class TestControlFlowSignalsDoNotMarkSpan:
    @pytest.mark.parametrize(
        "signal",
        [
            EarlyExitResponse("done"),
            EarlyAbort(),
            GenerationCancelled("cancelled"),
        ],
        ids=["early_exit", "early_abort", "generation_cancelled"],
    )
    def test_signal_is_reraised_without_marking_span(self, signal):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        with pytest.raises(type(signal)):
            _RaisesSignal(signal)(ctx)

        span.mark_span_as_error.assert_not_called()
        span.set_outputs.assert_called_once()

    def test_error_recorded_alongside_signal_is_context_not_error(self):
        """A degraded fallback recorded before an early exit is context, not a failure."""
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        with pytest.raises(EarlyExitResponse):
            _RecordsErrorThenEarlyExits()(ctx)

        span.mark_span_as_error.assert_not_called()
        assert span.set_outputs.call_args[0][0]["handled_errors"] == ["fallback used"]


class TestGenuineExceptionsPropagate:
    def test_raised_exception_propagates(self):
        """Genuine exceptions propagate so the tracer's span CM records the error."""
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        with pytest.raises(RuntimeError, match="kaboom"):
            _RaisesGenuineError()(ctx)

        # __call__ does not swallow or double-mark -- the span context manager owns this.
        span.mark_span_as_error.assert_not_called()


class TestHappyPath:
    def test_no_error_recorded_when_stage_succeeds(self):
        trace_service = make_trace_service()
        span = trace_service.span.return_value
        ctx = make_context(trace_service=trace_service)

        _HappyStage()(ctx)

        span.mark_span_as_error.assert_not_called()
        span.set_outputs.assert_called_once()
