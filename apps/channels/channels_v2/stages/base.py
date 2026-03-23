from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext


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

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage: check should_run, run inside a trace span."""
        if not self.should_run(ctx):
            return
        stage_name = self.__class__.__name__
        with ctx.trace_service.span(
            stage_name, inputs={}, notification_config=self.get_span_notification_config()
        ) as span:
            self.process(ctx)
            span.set_outputs({})
