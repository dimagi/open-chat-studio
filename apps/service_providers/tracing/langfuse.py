from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langfuse.callback import CallbackHandler
from loguru import logger
from typing_extensions import override

from .base import BaseTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler

    from .schema import Log


class LangFuseTracer(BaseTracer):
    flow_id: str

    def __init__(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str, config: dict):
        self.trace_id = trace_id
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_id = user_id
        self.spans: dict = OrderedDict()  # spans that are not ended

        self._ready: bool = self.setup_langfuse(config) if config else False

    @property
    def ready(self):
        return self._ready

    def setup_langfuse(self, config) -> bool:
        try:
            self._client = client_manager.get(config)
            self.trace = self._client.trace(
                id=str(self.trace_id), name=self.trace_name, session_id=self.session_id, user_id=self.user_id
            )
        except ImportError:
            logger.exception("Could not import langfuse. Please install it with `pip install langfuse`.")
            return False

        except Exception as e:  # noqa: BLE001
            logger.debug(f"Error setting up LangSmith tracer: {e}")
            return False

        return True

    @override
    def add_trace(
        self,
        trace_id: str,  # actually component id
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

        if len(self.spans) > 0:
            last_span = next(reversed(self.spans))
            span = self.spans[last_span].span(**content_span)
        else:
            span = self.trace.span(**content_span)

        self.spans[trace_id] = span

    @override
    def end_trace(
        self,
        trace_id: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
        logs: Sequence[Log | dict] = (),
    ) -> None:
        end_time = datetime.now(tz=UTC)
        if not self._ready:
            return

        span = self.spans.pop(trace_id, None)
        if span:
            output: dict = {}
            output |= outputs or {}
            output |= {"error": str(error)} if error else {}
            output |= {"logs": list(logs)} if logs else {}
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

    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        if not self._ready:
            return None

        # get callback from parent span
        stateful_client = self.spans[next(reversed(self.spans))] if len(self.spans) > 0 else self.trace
        return CustomCallbackHandler(stateful_client=stateful_client, update_stateful_client=False)


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
