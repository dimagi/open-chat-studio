from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from langsmith import Client, RunTree
from typing_extensions import override

from .base import BaseTracer, EventLevel, TraceInfo

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler


class LangSmithTracer(BaseTracer):
    provider_type = "langsmith"

    def __init__(self, client_config: dict):
        self.spans: dict[str, RunTree] = OrderedDict()  # spans identified by span_id
        self.root_run_tree: RunTree | None = None
        self._client = None
        self._config = client_config or {}
        self._ready: bool = self.setup_client() if client_config else False

    @property
    def ready(self):
        return self._ready and self._client is not None

    def setup_client(self) -> bool:
        try:
            self._client = Client(
                api_url=self._config.get("api_url", "https://api.smith.langchain.com"),
                api_key=self._config.get("api_key"),
            )
            return True
        except Exception as e:  # noqa: BLE001
            # Log the error or handle it as appropriate for your application
            print(f"Error setting up LangSmith client: {e}")
            return False

    @override
    def initialize(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        if not self.ready:
            return

        # Create a root run tree for this trace
        project_name = self._config.get("project", "default")

        # Add metadata for the trace
        metadata = {
            "session_id": session_id,
            "user_id": user_id,
        }

        # Initialize the root run tree
        self.root_run_tree = RunTree(
            id=trace_id,
            name=trace_name,
            run_type="chain",
            ls_client=self._client,
            project_name=project_name,
            extra={"metadata": metadata},
            tags=[f"user:{user_id}", f"session:{session_id}"],
        )
        self.root_run_tree.post()

    @override
    def start_span(
        self,
        span_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.ready or not self.root_run_tree:
            return

        # Determine the parent for this span
        # If there are existing spans, use the last one as parent, otherwise use the root
        parent = next(reversed(self.spans.values())) if self.spans else self.root_run_tree

        # Create a child span as a nested run tree
        span_tree = parent.create_child(
            name=trace_name,
            run_type="chain",
            inputs=inputs,
            extra={"metadata": metadata or {}},
        )
        span_tree.post()

        # Store the span for later reference
        self.spans[span_id] = span_tree

    @override
    def end_span(
        self,
        span_id: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        if not self.ready:
            return

        # Get the span and remove it from our tracking dict
        span_tree = self.spans.pop(span_id, None)
        if span_tree:
            span_tree.end(outputs=outputs or {}, error=str(error) if error else None)
            span_tree.patch()

    @override
    def event(
        self,
        name: str,
        message: str,
        level: EventLevel = "DEFAULT",
        metadata: dict[str, Any] | None = None,
    ):
        if not self.ready:
            return

        # Determine where to log this event
        # If there are spans, log to the most recent span
        # Otherwise log to the root run tree
        target = next(reversed(self.spans.values())) if self.spans else self.root_run_tree

        if target:
            target.add_event(
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
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.ready or not self.root_run_tree:
            return

        # End any remaining spans
        for span_id in self.spans:
            self.end_span(span_id, {}, error)

        self.root_run_tree.end(outputs=outputs, error=str(error) if error else None, metadata=metadata)
        self.root_run_tree.patch()

        # Clean up
        self.root_run_tree = None

    @override
    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        from langchain.callbacks.tracers import LangChainTracer

        if not self.ready:
            return None

        target = next(reversed(self.spans.values())) if self.spans else self.root_run_tree
        callback = LangChainTracer(
            project_name=self._config.get("project", "default"),
            client=self._client,
        )
        callback.latest_run = target
        return callback

    def get_current_trace_info(self) -> TraceInfo | None:
        if not self._ready or not self.root_run_tree:
            return None

        return TraceInfo(
            provider_type=self.trace_provider_type,
            trace_id=str(self.root_run_tree.id),
            trace_url=self.root_run_tree.get_url(),
        )
