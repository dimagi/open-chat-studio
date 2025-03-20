from __future__ import annotations

from typing import TYPE_CHECKING

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
        self._callback = None

    def get_callback(self, participant_id: str, session_id: str) -> BaseCallbackHandler:
        from langfuse.callback import CallbackHandler

        if self._callback:
            raise ServiceReentryException("Service does not support reentrant use.")

        self._callback = wrap_callback(CallbackHandler(user_id=participant_id, session_id=session_id, **self.config))
        return self._callback

    def get_trace_metadata(self) -> dict[str, str] | None:
        if not self._callback:
            raise ServiceNotInitializedException("Service not initialized.")

        if self._callback.trace:
            return {
                "trace_info": {
                    "trace_id": self._callback.trace.id,
                    "trace_url": self._callback.trace.get_trace_url(),
                },
                "trace_provider": self.type,
            }
