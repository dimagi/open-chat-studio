from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from django.db.models import Model

from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.service_providers.llm_service.runnables import GenerationCancelled

if TYPE_CHECKING:
    from apps.channels.pipeline import MessageProcessingContext

# Control-flow signals are not failures: they steer the pipeline (early exit,
# silent abort, generation cancellation) rather than indicate something broke.
# They must not mark a stage's span as errored.
_CONTROL_FLOW_SIGNALS = (EarlyExitResponse, EarlyAbort, GenerationCancelled)

# Bounds for span-value serialization -- keep trace payloads small and cheap.
_SPAN_LIST_LIMIT = 20
_SPAN_MAX_DEPTH = 2
_UNSET = object()

# Field-list declarations use the real context attribute names, but trace spans
# display friendlier keys free of legacy "experiment" naming. Applied per path
# segment, so "experiment_session.experiment_versions" -> "session.chatbot_versions".
_SPAN_KEY_ALIASES = {
    "experiment_session": "session",
    "experiment_channel": "channel",
    "experiment_versions": "chatbot_versions",
}


class ProcessingStage(ABC):
    """Base class for stateless processing stages.

    Stages are zero-arg -- all dependencies come via the context.
    Each stage is responsible for its own error handling.

    Stages do NOT check early_exit_response -- the pipeline orchestrator
    handles short-circuiting. To exit early, raise EarlyExitResponse.

    Observability: declare ``span_input_fields`` / ``span_output_fields`` as
    context attribute paths (dotted paths allowed, e.g. ``"experiment_session.status"``)
    to record them on the stage's trace span. Inputs are read before ``process``,
    outputs after. Override ``get_span_inputs`` / ``get_span_outputs`` directly
    when a derived value is needed instead of a raw context field.
    """

    # Context attribute paths recorded on this stage's trace span.
    span_input_fields: ClassVar[tuple[str, ...]] = ()
    span_output_fields: ClassVar[tuple[str, ...]] = ()

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
        """Context recorded on this stage's span, from ``span_input_fields``.
        Override for derived values that aren't raw context fields."""
        return _summarize_span_fields(ctx, self.span_input_fields)

    def get_span_outputs(self, ctx: MessageProcessingContext) -> dict[str, Any]:
        """Context recorded on this stage's span, from ``span_output_fields``.
        Override for derived values that aren't raw context fields."""
        return _summarize_span_fields(ctx, self.span_output_fields)

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


def _summarize_span_fields(ctx: MessageProcessingContext, field_paths: tuple[str, ...]) -> dict[str, Any]:
    """Read each context attribute path and render it trace-safe.

    Serialization must never break message processing, so any failure to read
    or render a field degrades to a placeholder rather than raising.
    """
    summary: dict[str, Any] = {}
    for path in field_paths:
        key = _span_key(path)
        try:
            value = _resolve_path(ctx, path)
            if value is _UNSET:
                continue
            summary[key] = _to_trace_value(value)
        except Exception:
            summary[key] = "<unserializable>"
    return summary


def _span_key(path: str) -> str:
    """Map a context attribute path to its display key, aliasing legacy segments."""
    return ".".join(_SPAN_KEY_ALIASES.get(part, part) for part in path.split("."))


def _resolve_path(obj: Any, path: str) -> Any:
    """Walk a dotted attribute path. Returns ``_UNSET`` if an attribute is
    missing, ``None`` if any step along the way is None."""
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, _UNSET)
        if obj is _UNSET:
            return _UNSET
    return obj


def _to_trace_value(value: Any, depth: int = 0) -> Any:
    """Render a value as JSON-safe primitives for a trace span.

    Models collapse to ``{id, model}`` (never ``str(model)`` -- that can fire
    queries); unknown objects collapse to their type name. Collections are
    bounded by size and depth.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Enum):
        return _to_trace_value(value.value, depth)
    if isinstance(value, date):  # date and datetime
        return value.isoformat()
    if isinstance(value, Model):
        return {"id": value.pk, "model": str(value._meta.verbose_name)}
    if isinstance(value, list | tuple | set):
        return _to_trace_collection(list(value), depth)
    if isinstance(value, dict):
        return _to_trace_mapping(value, depth)
    return f"<{type(value).__name__}>"


def _to_trace_collection(items: list, depth: int) -> Any:
    if depth >= _SPAN_MAX_DEPTH:
        return f"[{len(items)} items]"
    return [_to_trace_value(item, depth + 1) for item in items[:_SPAN_LIST_LIMIT]]


def _to_trace_mapping(mapping: dict, depth: int) -> Any:
    if depth >= _SPAN_MAX_DEPTH:
        return f"{{{len(mapping)} keys}}"
    return {str(k): _to_trace_value(v, depth + 1) for k, v in mapping.items()}
