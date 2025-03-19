from dataclasses import asdict, is_dataclass
from typing import Any, cast

from django.db.models import Model
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel

from apps.utils.proxy import Proxy


class CallbackWrapper(Proxy):
    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        inputs = serialize_input_output_dict(inputs)
        return self.target.on_chain_start(serialized, inputs, **kwargs)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        outputs = serialize_input_output_dict(outputs)
        return self.target.on_chain_end(outputs, **kwargs)


def wrap_callback(callback: BaseCallbackHandler) -> BaseCallbackHandler:
    return cast(CallbackWrapper(callback), BaseCallbackHandler)


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
