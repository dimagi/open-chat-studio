"""Ask Jev about a pipeline run's state, for the nodes that call it."""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import CallbackManager
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.chat.exceptions import ProviderConfigurationError
from apps.pipelines.models import PipelineChatHistoryModes
from apps.service_providers.llm_service.error_classification import AUTHENTICATION_MESSAGE
from apps.service_providers.llm_service.jev import (
    JevAuthenticationError,
    JevClient,
    JevResponse,
    Question,
    json_safe,
)
from apps.service_providers.models import LlmProviderTypes

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.nodes.nodes import RouterNode

    JevNode = RouterNode

# Jev reads at most 64k tokens per request. These bounds keep the conversation well inside that.
HISTORY_LIMIT = 20
MESSAGE_CHARACTER_LIMIT = 2000


def ask_jev(
    node: "JevNode", context: "NodeContext", questions: Mapping[str, Question], client: JevClient
) -> JevResponse:
    """Ask every question about the run's state in one request, and report the call's usage.

    Raises `JevError` when the request fails, and `ProviderConfigurationError` when Jev rejects the key.
    """
    model_name = node.repo.get_llm_provider_model(node.llm_provider_model_id).name
    state = json_safe(build_state(node, context))
    try:
        response = client.ask(model_name, state, questions)
    except JevAuthenticationError as e:
        raise ProviderConfigurationError(f"{AUTHENTICATION_MESSAGE} {e}") from e
    _report_call(node, model_name, _request_text(state, questions), response)
    return response


def build_state(node: "JevNode", context: "NodeContext") -> dict:
    """What Jev is asked about: the input, the history the node is configured to see, and the session's data."""
    history = node.get_history(exclude_message_id=context.input_message_id) if context.session else []
    return {
        "input": context.input,
        "history": [_history_entry(message) for message in _recent(history, _history_limit(node))],
        "participant_data": context.state.participant_data or {},
        "session_state": context.state.session_state or {},
        "temp_state": context.state.temp or {},
    }


def _history_limit(node: "JevNode") -> int:
    if node.get_history_mode() == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        return min(node.max_history_length, HISTORY_LIMIT)
    return HISTORY_LIMIT


def _recent(history: list[BaseMessage], limit: int) -> list[BaseMessage]:
    return history[-limit:] if limit > 0 else []


def _history_entry(message: BaseMessage) -> dict:
    return {"role": message.type, "content": message.text[:MESSAGE_CHARACTER_LIMIT]}


def _request_text(state: Any, questions: Mapping[str, Question]) -> str:
    return json.dumps({"state": state, "questions": {name: question.to_json() for name, question in questions.items()}})


def _report_call(node: "JevNode", model_name: str, request_text: str, response: JevResponse) -> None:
    """Report the call to the run's callbacks as an LLM call, so tracers and cost tracking see its usage.

    Reported only once a response is in hand: a failed call has no usage to record.
    """
    config = node._config or {}
    manager = CallbackManager.configure(
        inheritable_callbacks=config.get("callbacks"),
        inheritable_tags=config.get("tags"),
        inheritable_metadata=config.get("metadata"),
        local_metadata={"ocs_provider_type": str(LlmProviderTypes.jev), "ls_model_name": model_name},
    )
    (run,) = manager.on_llm_start({"name": "Jev"}, [request_text], invocation_params={"model": model_name})
    usage = response.usage
    message = AIMessage(
        content=json.dumps(response.answers),
        usage_metadata={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
        },
        response_metadata={"model_name": response.model},
    )
    run.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
