from dataclasses import asdict, is_dataclass
from typing import Any

from django.db.models import Model
from langchain_core.callbacks.manager import CallbackManager
from langchain_core.tracers import LangChainTracer
from pydantic import BaseModel


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


class TraceInfo(BaseModel):
    trace_id: str
    trace_url: str


class TraceService:
    def __init__(self, type_, config: dict):
        self.type = type_
        self.config = config

    def get_callback(self, participant_id: str, session_id: str):
        raise NotImplementedError

    def update_trace(self, metadata: dict):
        pass

    def get_current_trace_info(self) -> TraceInfo | None:
        return None

    def initialize_from_callback_manager(self, callback_manager: CallbackManager):
        pass


class LangFuseTraceService(TraceService):
    """
    Notes on langfuse:

    The API is designed to be used with a single set of credentials whereas we need to provide
    different credentials per call. This is why we don't use the standard 'observe' decorator.
    """

    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self._callback = None

    def get_callback(self, participant_id: str, session_id: str):
        from langfuse.callback import CallbackHandler

        if self._callback:
            raise ServiceReentryException("Service does not support reentrant use.")

        self._callback = CallbackWrapper(CallbackHandler(user_id=participant_id, session_id=session_id, **self.config))
        return self._callback

    def initialize_from_callback_manager(self, callback_manager: CallbackManager):
        """
        Populates the callback from the callback handler already configured in `callback_manager`. This allows the trace
        service to reuse existing callbacks.
        """

        for handler in callback_manager.handlers:
            if isinstance(handler, CallbackWrapper):
                self._callback = handler

    def update_trace(self, metadata: dict):
        if not metadata:
            return

        if not self._callback:
            raise ServiceNotInitializedException("Service not initialized.")

        self._callback.trace.update(metadata=metadata)

    def get_current_trace_info(self) -> TraceInfo | None:
        if not self._callback:
            raise ServiceNotInitializedException("Service not initialized.")

        if self._callback.trace:
            return TraceInfo(
                trace_id=self._callback.trace.id,
                trace_url=self._callback.trace.get_trace_url(),
            )


class LangSmithTraceService(TraceService):
    def get_callback(self, participant_id: str, session_id: str):
        from langsmith import Client

        client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        return LangChainTracer(client=client, project_name=self.config["project"])


class CallbackWrapper:
    def __init__(self, callback):
        self.callback = callback

    def __getattr__(self, item):
        return getattr(self.callback, item)

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        inputs = serialize_input_output_dict(inputs)
        return self.callback.on_chain_start(serialized, inputs, **kwargs)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        outputs = serialize_input_output_dict(outputs)
        return self.callback.on_chain_end(outputs, **kwargs)


def serialize_input_output_dict(data: dict[Any, Any]) -> dict[Any, Any]:
    """Ensure that dict values are serializable."""
    return safe_serialize(data)


def safe_serialize(obj: Any) -> Any:
    if isinstance(obj, list):
        return [safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {safe_serialize(k): safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, BaseModel):
        return safe_serialize(obj.model_dump())
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Model):
        return str(obj)
    return obj
