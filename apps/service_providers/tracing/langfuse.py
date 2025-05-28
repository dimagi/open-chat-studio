from __future__ import annotations

import atexit
import logging
import threading
import time
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langfuse.langchain import CallbackHandler

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException
from .const import SpanLevel

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler
    from langfuse import Langfuse


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
        self.current_trace_id = None
        self.spans: dict[UUID, str] = {}  # Maps span_id to langfuse observation_id

    @property
    def ready(self) -> bool:
        return bool(self.client and self.current_trace_id)

    def _start_trace_internal(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.current_trace_id:
            raise ServiceReentryException("Service does not support reentrant use.")

        self.client = client_manager.get(self.config)
        # In LangFuse v3, we need to start a new trace context
        # The trace_id will be used to identify this trace
        self.current_trace_id = str(trace_id)

        # Set initial metadata on the trace
        if metadata:
            metadata = dict(metadata)
        else:
            metadata = {}
        metadata.update(
            {
                "session_id": session_id,
                "user_id": user_id,
            }
        )

        # Start the trace context in LangFuse v3
        # We use update_current_trace to set initial properties
        try:
            # Note: LangFuse v3 API has changed significantly
            # For now, we'll start a span as the root and treat it as our trace
            self.client.start_span(
                name=trace_name,
                input=inputs,
                metadata=metadata,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.error(f"Failed to start LangFuse trace: {e}")
            self.current_trace_id = None
            raise

    def _end_trace_internal(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        try:
            # Update the current span (which is our root trace) with outputs
            if outputs or error:
                update_data = {}
                if outputs:
                    update_data["output"] = outputs
                if error:
                    update_data["level"] = "ERROR"
                    update_data["status_message"] = str(error)

                self.client.update_current_span(**update_data)

            # Flush any pending data
            self.client.flush()
        except Exception as e:
            logger.error(f"Failed to end LangFuse trace: {e}")
        finally:
            self.client = None
            self.current_trace_id = None
            self.spans.clear()

    def _start_span_internal(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        if not self.ready:
            return

        try:
            # Start a new span in LangFuse v3
            observation_id = self.client.start_span(
                name=span_name,
                input=inputs,
                metadata=metadata or {},
                level=level,
            )
            # Store the mapping from our span_id to LangFuse observation_id
            if observation_id:
                self.spans[span_id] = observation_id
        except Exception as e:
            logger.error(f"Failed to start LangFuse span: {e}")

    def _end_span_internal(
        self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        if not self.ready:
            return

        observation_id = self.spans.pop(span_id, None)
        if observation_id:
            try:
                # Update the span with outputs and end it
                update_data = {}
                if outputs:
                    update_data["output"] = outputs
                if error:
                    update_data["level"] = "ERROR"
                    update_data["status_message"] = str(error)

                # In LangFuse v3, we update the current span
                self.client.update_current_span(**update_data)
            except Exception as e:
                logger.error(f"Failed to end LangFuse span: {e}")

    def _set_span_attribute(self, span_id: UUID, key: str, value: Any) -> None:
        """Set an attribute on a LangFuse span."""
        if not self.ready:
            return

        observation_id = self.spans.get(span_id)
        if observation_id:
            self.client.update_current_span(metadata={key: value})

    def _record_span_exception(self, span_id: UUID, exception: Exception) -> None:
        """Record an exception on a LangFuse span."""
        if not self.ready:
            return

        observation_id = self.spans.get(span_id)
        if observation_id:
            self.client.update_current_span(
                level="ERROR", status_message=str(exception), output={"error": str(exception)}
            )

    # Backward compatibility methods
    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Deprecated: Use trace() context manager instead."""
        super().start_trace(trace_name, trace_id, session_id, user_id, inputs, metadata)
        self._start_trace_internal(trace_name, trace_id, session_id, user_id, inputs, metadata)

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use trace() context manager instead."""
        self._end_trace_internal(outputs, error)
        super().end_trace(outputs, error)

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        """Deprecated: Use span() context manager instead."""
        self._start_span_internal(span_id, span_name, inputs, metadata, level)

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use span() context manager instead."""
        self._end_span_internal(span_id, outputs, error)

    def get_langchain_callback(self) -> BaseCallbackHandler:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        # In LangFuse v3, the CallbackHandler is initialized differently
        return CallbackHandler(
            public_key=self.config.get("public_key"),
            secret_key=self.config.get("secret_key"),
            host=self.config.get("host"),
            user_id=self.user_id,
            session_id=self.session_id,
        )

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")

        try:
            trace_url = self.client.get_trace_url()
        except Exception:
            trace_url = ""

        return {
            "trace_id": self.current_trace_id or "",
            "trace_url": trace_url,
            "trace_provider": self.type,
        }


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


# Note: LangFuse v3 CallbackHandler is now imported from langfuse.langchain
# The custom event handling may need to be updated based on the new API
