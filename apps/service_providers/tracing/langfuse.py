from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langfuse.callback import CallbackHandler
from langfuse.client import StatefulClient
from loguru import logger
from typing_extensions import override

from .base import BaseTracer, EventLevel

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler


class LangFuseTracer(BaseTracer):
    def __init__(self, config: dict):
        self.spans: dict = OrderedDict()  # spans that are not ended
        self.trace = None
        self._client = None
        self._ready: bool = self.setup_langfuse(config) if config else False

    @property
    def ready(self):
        return self._ready

    def setup_langfuse(self, config) -> bool:
        try:
            self._client = client_manager.get(config)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Error setting up Langfuse tracer: {e}")
            return False
        return True

    def initialize(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        self.trace = self._client.trace(id=str(trace_id), name=trace_name, session_id=session_id, user_id=user_id)

    @override
    def start_span(
        self,
        span_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        start_time = datetime.now(tz=UTC)
        if not self._ready:
            return

        content_span = {
            "name": trace_name,
            "input": inputs,
            "metadata": metadata or {},
            "start_time": start_time,
        }

        self.spans[span_id] = self._get_current_span().span(**content_span)

    @override
    def end_span(
        self,
        span_id: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        end_time = datetime.now(tz=UTC)
        if not self._ready:
            return

        span = self.spans.pop(span_id, None)
        if span:
            output: dict = {}
            output |= outputs or {}
            output |= {"error": str(error)} if error else {}
            content = {"output": output, "end_time": end_time}
            span.update(**content)

    @override
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._ready:
            return

        print(inputs)
        print(outputs)
        self._client.flush()

    @override
    def event(self, name: str, message: str, level: EventLevel = "DEFAULT", metadata: dict[str, Any] | None = None):
        if not self._ready:
            return None

        self._get_current_span().event(
            name=name,
            status_message=message,
            level=level,
            metadata=metadata or {},
        )

    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        if not self._ready:
            return None

        # get callback from parent span
        stateful_client = self.spans[next(reversed(self.spans))] if len(self.spans) > 0 else self.trace
        return CustomCallbackHandler(stateful_client=stateful_client, update_stateful_client=False)

    def _get_current_span(self) -> StatefulClient:
        if len(self.spans) > 0:
            last_span = next(reversed(self.spans))
            return self.spans[last_span]
        else:
            return self.trace


class CustomCallbackHandler(CallbackHandler):
    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if "experiment_session" in inputs:
            from apps.pipelines.nodes.base import PipelineState

            try:
                inputs = PipelineState(**inputs).json_safe()
            except Exception:
                self._log_debug_event("Could not convert inputs to PipelineState")

        return super().on_chain_start(
            serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, tags=tags, metadata=metadata, **kwargs
        )


class ClientManager:
    def __init__(self) -> None:
        self.clients: dict[int, Any] = {}

    def get(self, config: dict) -> Any:
        key = hash(frozenset(config.items()))
        if key not in self.clients:
            print("createing new client for key", key)
            self.clients[key] = self._create_client(config)
        return self.clients[key]

    def _create_client(self, config: dict):
        from langfuse import Langfuse

        return Langfuse(**config)


client_manager = ClientManager()
