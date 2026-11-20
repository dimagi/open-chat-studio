"""Ask a decision backend about a pipeline run's state, for the nodes that call one."""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langchain_core.callbacks import CallbackManager
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.chat.exceptions import ProviderConfigurationError
from apps.pipelines.models import PipelineChatHistoryModes
from apps.service_providers.llm_service.decision_backend import (
    DecisionAuthenticationError,
    DecisionBackend,
    DecisionResponse,
    Question,
)
from apps.service_providers.llm_service.error_classification import AUTHENTICATION_MESSAGE

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.nodes.decision_node import DecisionNode
    from apps.pipelines.nodes.nodes import RouterNode

    DecidingNode = RouterNode | DecisionNode

# These bounds keep the conversation well inside a decision model's context; Jev reads at most 64k tokens.
HISTORY_LIMIT = 20
MESSAGE_CHARACTER_LIMIT = 2000


def ask_decisions(
    node: "DecidingNode", context: "NodeContext", questions: Mapping[str, Question], backend: DecisionBackend
) -> DecisionResponse:
    """Ask every question about the run's state in one request, and report the call's usage.

    Raises `DecisionError` when the request fails, and `ProviderConfigurationError` when the provider
    rejects the credentials.
    """
    model_name = node.repo.get_llm_provider_model(node.llm_provider_model_id).name
    state = build_state(node, context)
    try:
        response = backend.ask(model_name, state, questions)
    except DecisionAuthenticationError as e:
        raise ProviderConfigurationError(f"{AUTHENTICATION_MESSAGE} {e}") from e
    _report_call(node, backend, model_name, backend.request_text(state, questions), response)
    return response


def build_state(node: "DecidingNode", context: "NodeContext") -> dict:
    """What the model is asked about: the input, the history the node is configured to see, and the session's data."""
    history = node.get_history(exclude_message_id=context.input_message_id) if context.session else []
    return {
        "input": context.input,
        "history": [_history_entry(message) for message in _recent(history, _history_limit(node))],
        "participant_data": context.state.participant_data or {},
        "session_state": context.state.session_state or {},
        "temp_state": context.state.temp or {},
    }


def _history_limit(node: "DecidingNode") -> int:
    if node.get_history_mode() == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        return min(node.max_history_length, HISTORY_LIMIT)
    return HISTORY_LIMIT


def _recent(history: list[BaseMessage], limit: int) -> list[BaseMessage]:
    return history[-limit:] if limit > 0 else []


def _history_entry(message: BaseMessage) -> dict:
    return {"role": message.type, "content": message.text[:MESSAGE_CHARACTER_LIMIT]}


def _report_call(
    node: "DecidingNode", backend: DecisionBackend, model_name: str, request_text: str, response: DecisionResponse
) -> None:
    """Report the call to the run's callbacks as an LLM call, so tracers and cost tracking see its usage.

    Reported only once a response is in hand: a failed call has no usage to record.
    """
    config = node._config or {}
    manager = CallbackManager.configure(
        inheritable_callbacks=config.get("callbacks"),
        inheritable_tags=config.get("tags"),
        inheritable_metadata=config.get("metadata"),
        local_metadata={"ocs_provider_type": backend.provider_type, "ls_model_name": model_name},
    )
    (run,) = manager.on_llm_start({"name": backend.name}, [request_text], invocation_params={"model": model_name})
    usage = response.usage
    message = AIMessage(
        content=json.dumps(response.raw_answers),
        usage_metadata={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
        },
        response_metadata={"model_name": response.model},
    )
    run.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
