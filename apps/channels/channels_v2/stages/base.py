from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from apps.channels.channels_v2.exceptions import EarlyAbort, EarlyExitResponse
from apps.service_providers.llm_service.runnables import GenerationCancelled

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext

# Control-flow signals are not failures: they steer the pipeline (early exit,
# silent abort, generation cancellation) rather than indicate something broke.
# They must not mark a stage's span as errored.
_CONTROL_FLOW_SIGNALS = (EarlyExitResponse, EarlyAbort, GenerationCancelled)


class ProcessingStage(ABC):
    """Base class for stateless processing stages.

    Stages are zero-arg -- all dependencies come via the context.
    Each stage is responsible for its own error handling.

    Stages do NOT check early_exit_response -- the pipeline orchestrator
    handles short-circuiting. To exit early, raise EarlyExitResponse.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Override to add stage-specific preconditions.
        Default: always run. NOTE: This is NOT for early exit checking --
        the pipeline handles that."""
        return True

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process the context, modifying it in place.
        Raise EarlyExitResponse to short-circuit the pipeline."""

    def get_span_notification_config(self):
        """Override to attach a SpanNotificationConfig to this stage's trace span.
        Default: None (no notification)."""
        return None

    def get_span_inputs(self, ctx: MessageProcessingContext) -> dict[str, Any]:
        """Override to record useful context on this stage's trace span.
        Default: empty -- no inputs recorded."""
        return {}

    def get_span_outputs(self, ctx: MessageProcessingContext) -> dict[str, Any]:
        """Override to record useful outputs on this stage's trace span.
        Default: empty -- no outputs recorded."""
        return {}

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage inside a trace span, keeping the span's status honest.

        Three outcomes are distinguished:

        1. ``process`` raises a genuine exception -- it propagates out of the
           span and the tracer records the span (and trace) as errored.
        2. ``process`` raises a control-flow signal (early exit / abort /
           cancellation) -- the signal is caught, the span closes cleanly, and
           the signal is re-raised only after the span has exited so the tracer
           does not mistake steering for failure.
        3. ``process`` catches a failure itself and records it on the context
           (``sending_exceptions`` / ``processing_errors``) instead of raising
           -- those newly recorded errors are surfaced on the span so the trace
           still reflects that something went wrong.
        """
        if not self.should_run(ctx):
            return

        stage_name = self.__class__.__name__
        sending_before = len(ctx.sending_exceptions)
        errors_before = len(ctx.processing_errors)
        deferred_signal: Exception | None = None

        with ctx.trace_service.span(
            stage_name, inputs=self.get_span_inputs(ctx), notification_config=self.get_span_notification_config()
        ) as span:
            try:
                self.process(ctx)
            except _CONTROL_FLOW_SIGNALS as signal:
                deferred_signal = signal

            outputs = self.get_span_outputs(ctx)
            messages, root_exc = self._new_handled_errors(ctx, sending_before, errors_before)
            if messages:
                outputs = {**outputs, "handled_errors": messages}
                # Only a genuine failure the stage recovered from marks the span
                # as errored. Errors recorded alongside a control-flow signal
                # (e.g. a degraded fallback response) stay as context, not errors.
                if deferred_signal is None:
                    span.mark_span_as_error("; ".join(messages), exception=root_exc)
            span.set_outputs(outputs)

        if deferred_signal is not None:
            raise deferred_signal

    @staticmethod
    def _new_handled_errors(
        ctx: MessageProcessingContext, sending_before: int, errors_before: int
    ) -> tuple[list[str], Exception | None]:
        """Return errors the stage recorded on the context during this run.

        Returns the human-readable messages and, when a sending exception was
        recorded, the underlying exception (so the span gets a real traceback).
        """
        new_sending = ctx.sending_exceptions[sending_before:]
        new_processing = ctx.processing_errors[errors_before:]
        messages = [str(exc) for exc in new_sending] + list(new_processing)
        root_exc: Exception | None = None
        if new_sending:
            first = new_sending[0]
            root_exc = getattr(first, "original_exc", first)
        return messages, root_exc
