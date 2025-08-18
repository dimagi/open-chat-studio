from __future__ import annotations

import atexit
import logging
import threading
import time
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langfuse.callback import CallbackHandler
from langfuse.client import StatefulSpanClient, StatefulTraceClient

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException
from .const import SpanLevel

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler
    from langfuse import Langfuse

    from apps.experiments.models import ExperimentSession


logger = logging.getLogger("ocs.tracing.langfuse")


class LangFuseTracer(Tracer):
    """
    Notes on langfuse:

    The API is designed to be used with a single set of credentials whereas we need to provide
    different credentials per call. This is why we don't use the standard 'observe' decorator.
    """

    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self.client = None
        self.trace: StatefulTraceClient | None = None
        self.spans: dict[UUID, StatefulSpanClient] = {}

    @property
    def ready(self) -> bool:
        return bool(self.trace)

    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.trace:
            raise ServiceReentryException("Service does not support reentrant use.")

        super().start_trace(trace_name, trace_id, session, inputs)

        self.client = client_manager.get(self.config)
        self.trace = self.client.trace(
            name=trace_name,
            session_id=str(session.external_id),
            user_id=session.participant.identifier,
            input=inputs,
            metadata=metadata,
        )

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        super().end_trace(outputs=outputs, error=error)
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        if outputs or error:
            outputs = outputs or {}
            outputs = {**outputs, "error": str(error)} if error else outputs
            self.trace.update(output=outputs)

        self.client.flush()
        self.client = None
        self.trace = None
        self.spans.clear()

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        if not self.ready:
            return

        content_span = {
            "id": str(span_id),
            "name": span_name,
            "input": inputs,
            "metadata": metadata or {},
            "level": level,
        }

        self.spans[span_id] = self._get_current_observation().span(**content_span)

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.ready:
            return

        span = self.spans.pop(span_id, None)
        if span:
            output: dict = {}
            output |= outputs or {}
            output |= {"error": str(error)} if error else {}

            content = {
                "output": output,
                "status_message": str(error) if error else None,
                "level": "ERROR" if error else None,
            }
            span.end(**content)

    def get_langchain_callback(self) -> BaseCallbackHandler:
        if not self.ready:
            raise ServiceReentryException("Service does not support reentrant use.")

        return LangfuseCallbackHandler(stateful_client=self._get_current_observation(), update_stateful_client=False)

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        return {
            "trace_id": self.trace.id,
            "trace_url": self.trace.get_trace_url(),
            "trace_provider": self.type,
        }

    def _get_current_observation(self) -> StatefulTraceClient | StatefulSpanClient:
        """
        Returns the most recent active span if one exists, otherwise returns the root trace.
        This ensures new spans are properly nested under their parent spans.
        """
        if self.spans:
            last_span = next(reversed(self.spans))
            return self.spans[last_span]
        else:
            return self.trace

    def add_trace_tags(self, tags: list[str]) -> None:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")
        self.trace.update(tags=tags)

    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    def set_input_message_id(self, input_message_id: str) -> None:
        pass


class ClientManager:
    """This class manages the langfuse clients to avoid creating a new client for every request.
    On requests for a client it will also remove any clients that have been inactive for a
    certain amount of time."""

    def __init__(self, stale_timeout=300, prune_interval=60, max_clients=20) -> None:
        self.clients: dict[int, tuple[float, Any]] = {}
        self.stale_timeout = stale_timeout
        self.max_clients = max_clients
        self.prune_interval = prune_interval
        self.lock = RLock()
        self._start_prune_thread()

    def get(self, config: dict) -> Langfuse:
        from langfuse import Langfuse

        key = hash(frozenset(config.items()))
        with self.lock:
            if key not in self.clients:
                logger.debug("Creating new client with key '%s'", key)
                client = Langfuse(**config)
            else:
                client = self.clients[key][1]
            self.clients[key] = (time.time(), client)
        return client

    def _start_prune_thread(self):
        self._prune_thread = threading.Thread(target=self._prune_worker, daemon=True)
        self._prune_thread.start()

    def _prune_worker(self):
        while True:
            time.sleep(self.prune_interval)
            self._prune_stale()

    def _prune_stale(self):
        if not self.clients:
            return

        logger.debug("Pruning clients...")
        for key in list(self.clients.keys()):
            timestamp, client = self.clients[key]
            if time.time() - timestamp > self.stale_timeout:
                logger.debug("Pruning old client with key '%s'", key)
                self._remove_client(key, client)

        if len(self.clients) > self.max_clients:
            # remove the oldest clients until we are below the max
            sorted_clients = sorted(self.clients.items(), key=lambda x: x[1][0])
            clients_to_remove = sorted_clients[: len(self.clients) - self.max_clients]
            logger.debug("Pruned %d clients above max limit", len(clients_to_remove))
            for key, (_, client) in clients_to_remove:
                self._remove_client(key, client)

    def _remove_client(self, key, client):
        with self.lock:
            self.clients.pop(key)
        client.shutdown()

    def shutdown(self):
        if not self.clients:
            return

        with self.lock:
            logger.debug("Shutting down all langfuse clients (%s)", len(self.clients))
            for _key, (_, client) in self.clients.items():
                client.shutdown()
            self.clients = {}


client_manager = ClientManager()


@atexit.register
def _shutdown():
    """Shutdown the client manager when the program exits."""
    client_manager.shutdown()


class LangfuseCallbackHandler(CallbackHandler):
    """Langfuse callback handler for LangChain that supports custom events"""

    def on_custom_event(
        self,
        name: str,
        data: Any,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if self.runs.get(run_id):
            self.runs[run_id].event(name=name, input=data, metadata=metadata)
            return

        if self.root_span is not None:
            self.root_span.event(name=name, input=data, metadata=metadata)
            return

        if self.trace is not None:
            self.trace.event(name=name, input=data, metadata=metadata)
            return
