import re
from dataclasses import asdict, is_dataclass
from typing import Any

from django.db.models import Model
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel

from apps.utils.proxy import Proxy

LANGSMITH_TAG_HIDDEN: str = "langsmith:hidden"


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


def wrap_callback(
    callback: BaseCallbackHandler, run_name_map: dict[str, str], filter_patterns: list[str]
) -> BaseCallbackHandler:
    """Wrap a callback handler to ensure that dict values are serializable.

    This method is really just for type compatibility with the original code.
    Note that using `cast` breaks the callback somehow. Presumably langchain is doing
    some magic with the type annotations."""

    if run_name_map or filter_patterns:
        callback = NameMappingWrapper(callback, run_name_map or {}, filter_patterns or [])  # ty: ignore[invalid-assignment]

    return CallbackWrapper(callback)  # ty: ignore[invalid-return-type]


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


class NameMappingWrapper(Proxy):
    def __init__(self, callback: BaseCallbackHandler, name_map: dict[str, str], filter_patterns: list[str]):
        super().__init__(callback)
        self.name_map = name_map
        self.filter_patterns = [re.compile(pattern) for pattern in filter_patterns]

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        tags, kwargs = self._update_tags_and_name(serialized, tags, kwargs)
        self.target.on_llm_start(serialized, prompts, tags=tags, **kwargs)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list],
        *,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        tags, kwargs = self._update_tags_and_name(serialized, tags, kwargs)
        self.target.on_chat_model_start(serialized, messages, tags=tags, **kwargs)

    def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        tags, kwargs = self._update_tags_and_name(serialized, tags, kwargs)
        self.target.on_retriever_start(serialized, query, tags=tags, **kwargs)

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        tags, kwargs = self._update_tags_and_name(serialized, tags, kwargs)
        self.target.on_chain_start(serialized, inputs, tags=tags, **kwargs)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        tags, kwargs = self._update_tags_and_name(serialized, tags, kwargs)
        self.target.on_tool_start(serialized, input_str, tags=tags, **kwargs)

    def _update_tags_and_name(
        self, serialized: dict[str, Any], tags: list[str] | None, kwargs: dict
    ) -> tuple[list[str], dict]:
        tags = tags or []
        if LANGSMITH_TAG_HIDDEN not in tags:
            name = get_langchain_run_name(serialized, **kwargs)
            kwargs["name"] = self.name_map.get(name, name)
            for pattern in self.filter_patterns:
                if pattern.search(name):
                    tags.append(LANGSMITH_TAG_HIDDEN)
                    break
        return tags, kwargs


def get_langchain_run_name(serialized: dict[str, Any] | None, **kwargs: Any) -> str:
    """Retrieve the name of a serialized LangChain runnable.

    The prioritization for the determination of the run name is as follows:
    - The value assigned to the "name" key in `kwargs`.
    - The value assigned to the "name" key in `serialized`.
    - The last entry of the value assigned to the "id" key in `serialized`.
    - "<unknown>".

    Args:
        serialized (Optional[Dict[str, Any]]): A dictionary containing the runnable's serialized data.
        **kwargs (Any): Additional keyword arguments, potentially including the 'name' override.

    Returns:
        str: The determined name of the Langchain runnable.
    """
    if "name" in kwargs and kwargs["name"] is not None:
        return kwargs["name"]

    try:
        return serialized["name"]  # ty: ignore[not-subscriptable]
    except (KeyError, TypeError):
        pass

    try:
        return serialized["id"][-1]  # ty: ignore[not-subscriptable]
    except (KeyError, TypeError):
        pass

    return "<unknown>"
