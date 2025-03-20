from dataclasses import asdict, is_dataclass
from typing import Any

from django.db.models import Model
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

    def get_trace_metadata(self) -> TraceInfo | None:
        return None


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
