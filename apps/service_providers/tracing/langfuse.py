from __future__ import annotations

import atexit
import logging
import threading
import time
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import UUID

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException
from .callback import wrap_callback

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
        self.trace = None

    @property
    def ready(self) -> bool:
        return bool(self.trace)

    def begin_trace(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        if self.trace:
            raise ServiceReentryException("Service does not support reentrant use.")

        super().begin_trace(trace_name, trace_id, session_id, user_id)

        self.client = client_manager.get(self.config)
        self.trace = self.client.trace(name=trace_name, session_id=session_id, user_id=user_id)

    def start_span(
        self, span_id: str, span_name: str, inputs: dict[str, Any], metadata: dict[str, Any] | None = None
    ) -> None:
        # TODO: add implementation
        pass

    def end_span(self, span_id: str, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        # TODO: add implementation
        pass

    def get_langchain_callback(self) -> BaseCallbackHandler:
        from langfuse.callback import CallbackHandler

        if not self.trace:
            raise ServiceReentryException("Service does not support reentrant use.")

        callback = CallbackHandler(
            stateful_client=self.trace,
            update_stateful_client=True,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        return wrap_callback(callback)

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.trace:
            raise ServiceNotInitializedException("Service not initialized.")

        return {
            "trace_id": self.trace.id,
            "trace_url": self.trace.get_trace_url(),
            "trace_provider": self.type,
        }

    def end_trace(self):
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")
        self.client.flush()


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
            for key, (_, client) in self.clients.items():
                client.shutdown()
            self.clients = {}


client_manager = ClientManager()


@atexit.register
def _shutdown():
    """Shutdown the client manager when the program exits."""
    client_manager.shutdown()
