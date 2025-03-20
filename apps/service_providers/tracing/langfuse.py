from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from . import TraceService
from .base import ServiceNotInitializedException, ServiceReentryException
from .callback import wrap_callback

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


class LangFuseTraceService(TraceService):
    """
    Notes on langfuse:

    The API is designed to be used with a single set of credentials whereas we need to provide
    different credentials per call. This is why we don't use the standard 'observe' decorator.
    """

    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self.trace = None

    def get_callback(self, trace_name: str, participant_id: str, session_id: str) -> BaseCallbackHandler:
        from langfuse.callback import CallbackHandler

        if self.trace:
            raise ServiceReentryException("Service does not support reentrant use.")

        client = client_manager.get(self.config)
        trace = client.trace(name=trace_name, session_id=session_id, user_id=participant_id)
        self.trace = trace
        callback = CallbackHandler(
            stateful_client=trace,
            update_stateful_client=False,
            user_id=participant_id,
            session_id=session_id,
        )
        return wrap_callback(callback)

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.trace:
            raise ServiceNotInitializedException("Service not initialized.")

        return {
            "trace_info": {
                "trace_id": self.trace.id,
                "trace_url": self.trace.get_trace_url(),
            },
            "trace_provider": self.type,
        }


class ClientManager:
    """This class manages the langfuse clients to avoid creating a new client for every request.
    On requests for a client it will also remove any clients that have been inactive for a
    certain amount of time."""

    def __init__(self, stale_timeout=300) -> None:
        self.clients: dict[int, tuple[float, Any]] = {}
        self.stale_timeout = stale_timeout

    def get(self, config: dict) -> Any:
        key = hash(frozenset(config.items()))
        if key not in self.clients:
            client = self._create_client(config)
        else:
            client = self.clients[key][1]
            self._prune_stale(key)
        self.clients[key] = (time.time(), client)
        return client

    def _create_client(self, config: dict):
        from langfuse import Langfuse

        return Langfuse(**config)

    def _prune_stale(self, exclude_key):
        for key in list(self.clients.keys()):
            if key == exclude_key:
                continue
            timestamp, client = self.clients[key]
            if time.time() - timestamp > self.stale_timeout:
                client.shutdown()
                self.clients.pop(key)


client_manager = ClientManager()
