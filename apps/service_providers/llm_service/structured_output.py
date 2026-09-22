from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from apps.service_providers.llm_service.outcomes import provider_reason, refusal_text


class NoStructuredOutputError(Exception):
    """The model completed the call without producing the requested structured result."""

    def __init__(self, model_text: str = "", reason: str = ""):
        self.model_text = model_text
        self.reason = reason
        detail = model_text or reason or "no content"
        super().__init__(f"The model did not return structured output: {detail}")


def structured_output_runnable(llm: BaseChatModel, schema: type[BaseModel]) -> Runnable[Any, BaseModel]:
    """Return a runnable that yields a `schema` instance or raises `NoStructuredOutputError`."""
    # Parser errors are captured as `parsing_error` inside this step, so an OutputParserException
    # escaping it is the provider adapter reporting that the model made no tool call.
    structured = llm.with_structured_output(schema, include_raw=True).with_fallbacks(
        [RunnableLambda(_raise_no_tool_call)],
        exceptions_to_handle=(OutputParserException,),
    )
    return structured.pipe(RunnableLambda(unwrap_structured_output))


def _raise_no_tool_call(_input: Any) -> dict:
    raise NoStructuredOutputError(reason="no tool call")


def unwrap_structured_output(result: dict) -> BaseModel:
    if result["parsed"] is not None:
        return result["parsed"]
    raw = result["raw"]
    refusal = refusal_text(raw)
    if result["parsing_error"] is not None and not refusal:
        raise result["parsing_error"]
    raise NoStructuredOutputError(model_text=refusal or raw.text, reason=provider_reason(raw))
