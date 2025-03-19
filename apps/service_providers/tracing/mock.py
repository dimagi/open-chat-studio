from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID, uuid4

from typing_extensions import override

from .base import BaseTracer, EventLevel, TraceInfo

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


class MockTracer(BaseTracer):
    """
    A mock implementation of BaseTracer for testing purposes.

    This tracer records all operations performed on it, allowing test code to verify
    that the correct tracing calls were made. It doesn't actually send data to any
    external service.

    Usage in tests:
        mock_tracer = MockTracer()
        tracer_wrapper = TracingServiceWrapper([mock_tracer])

        # Use the tracer in your code...

        # Then verify the calls:
        assert mock_tracer.initialize_calls[0]["trace_name"] == "expected_name"
        assert len(mock_tracer.span_starts) == 2  # Expected 2 spans started
        assert "error" in mock_tracer.events
    """

    # Class variables to track calls across instances
    reset_calls: ClassVar[list[str]] = []

    def __init__(self, client_config: dict = None):
        """
        Initialize a new mock tracer.

        Args:
            client_config: Optional configuration dictionary (not used in mock)
        """
        self.initialize_calls: list[dict[str, Any]] = []
        self.span_starts: dict[str, dict[str, Any]] = {}
        self.span_ends: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.end_calls: list[dict[str, Any]] = []
        self.trace_info_calls: list[str] = []
        self.last_run_id: UUID | None = None

        # Always ready for testing
        self._ready = True

        # To store the most recent trace context for callback testing
        self.current_trace: dict[str, Any] = {}

        # Track callbacks created
        self.callbacks_created: list[dict[str, Any]] = []

    @classmethod
    def reset(cls):
        """Reset all static tracking across instances"""
        cls.reset_calls.append(str(uuid4()))

    @property
    def ready(self) -> bool:
        """Whether the tracer is ready to use"""
        return self._ready

    @override
    def initialize(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        """Record an initialize call"""
        self.initialize_calls.append(
            {
                "trace_name": trace_name,
                "trace_id": trace_id,
                "session_id": session_id,
                "user_id": user_id,
            }
        )
        self.last_run_id = trace_id
        self.current_trace = {
            "name": trace_name,
            "id": trace_id,
            "session_id": session_id,
            "user_id": user_id,
        }

    @override
    def start_span(
        self,
        span_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] = None,
    ) -> None:
        """Record a span start"""
        self.span_starts[span_id] = {
            "trace_name": trace_name,
            "inputs": inputs,
            "metadata": metadata or {},
        }

    @override
    def end_span(
        self,
        span_id: str,
        outputs: dict[str, Any] = None,
        error: Exception = None,
    ) -> None:
        """Record a span end"""
        self.span_ends[span_id] = {
            "outputs": outputs or {},
            "error": str(error) if error else None,
        }

    @override
    def event(
        self,
        name: str,
        message: str,
        level: EventLevel = "DEFAULT",
        metadata: dict[str, Any] = None,
    ) -> None:
        """Record an event"""
        self.events.append(
            {
                "name": name,
                "message": message,
                "level": level,
                "metadata": metadata or {},
            }
        )

    @override
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception = None,
        metadata: dict[str, Any] = None,
    ) -> None:
        """Record the end of tracing"""
        self.end_calls.append(
            {
                "inputs": inputs,
                "outputs": outputs,
                "error": str(error) if error else None,
                "metadata": metadata or {},
            }
        )
        self.current_trace = {}

    @override
    def get_langchain_callback(self) -> "BaseCallbackHandler":
        """Create a mock callback handler for testing"""
        from langchain.callbacks.base import BaseCallbackHandler

        # Simple callback handler that just logs method calls
        class MockCallbackHandler(BaseCallbackHandler):
            def __init__(self, mock_tracer):
                self.mock_tracer = mock_tracer
                self.calls = []

            def on_llm_start(self, *args, **kwargs):
                self.calls.append({"type": "on_llm_start", "args": args, "kwargs": kwargs})
                return None

            def on_llm_end(self, *args, **kwargs):
                self.calls.append({"type": "on_llm_end", "args": args, "kwargs": kwargs})
                return None

            def on_chain_start(self, *args, **kwargs):
                self.calls.append({"type": "on_chain_start", "args": args, "kwargs": kwargs})
                return None

            def on_chain_end(self, *args, **kwargs):
                self.calls.append({"type": "on_chain_end", "args": args, "kwargs": kwargs})
                return None

            def on_tool_start(self, *args, **kwargs):
                self.calls.append({"type": "on_tool_start", "args": args, "kwargs": kwargs})
                return None

            def on_tool_end(self, *args, **kwargs):
                self.calls.append({"type": "on_tool_end", "args": args, "kwargs": kwargs})
                return None

        mock_handler = MockCallbackHandler(self)
        self.callbacks_created.append({"handler": mock_handler})
        return mock_handler

    @override
    def get_current_trace_info(self) -> "TraceInfo | None":
        """Return information about the current trace for UI display"""
        self.trace_info_calls.append(str(uuid4()))

        if not self._ready or not self.current_trace:
            return None

        return TraceInfo(
            provider_type="mock",
            trace_id=str(self.current_trace.get("id", "")),
            trace_url=f"mock-trace-url/{self.current_trace.get('id', '')}",
        )


class RecordingTracerContextManager:
    """
    A context manager for use in tests that provides a pre-configured
    MockTracer and TracingServiceWrapper.

    Usage:

    def test_my_feature():
        with RecordingTracerContextManager() as ctx:
            # use ctx.tracer in your test
            my_function_that_uses_tracing(ctx.tracer_wrapper)

            # Make assertions
            assert len(ctx.mock_tracer.span_starts) == 2
            assert "error" not in [e["level"] for e in ctx.mock_tracer.events]
    """

    def __init__(self):
        from .trace_service import TracingServiceWrapper

        self.mock_tracer = MockTracer()
        self.tracer_wrapper = TracingServiceWrapper([self.mock_tracer])

    def __enter__(self):
        MockTracer.reset()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
