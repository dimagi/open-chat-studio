"""Route an LLM Router node with one Jev Choice question instead of an LLM agent."""

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import CallbackManager
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.chat.exceptions import ProviderConfigurationError
from apps.service_providers.llm_service.error_classification import AUTHENTICATION_MESSAGE
from apps.service_providers.llm_service.jev import (
    ChoiceQuestion,
    JevAuthenticationError,
    JevClient,
    JevError,
    JevResponse,
    json_safe,
)
from apps.service_providers.models import LlmProviderTypes

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.nodes.nodes import RouterNode

logger = logging.getLogger("ocs.pipelines.nodes")

QUESTION_NAME = "route"
# Jev reads at most 64k tokens per request. These bounds keep the conversation well inside that.
HISTORY_LIMIT = 20
MESSAGE_CHARACTER_LIMIT = 2000


def route_with_jev(node: "RouterNode", context: "NodeContext", instructions: str, client: JevClient) -> str | None:
    """The keyword Jev chose, or None when the router should take its default route."""
    model_name = node.repo.get_llm_provider_model(node.llm_provider_model_id).name
    state = json_safe(build_state(node, context))
    questions = {QUESTION_NAME: ChoiceQuestion(criteria=_criteria(node), instructions=instructions)}
    try:
        response = client.ask(model_name, state, questions)
    except JevAuthenticationError as e:
        raise ProviderConfigurationError(f"{AUTHENTICATION_MESSAGE} {e}") from e
    except JevError as e:
        logger.warning("Router %s got no route from Jev: %s", node.name, e)
        return None
    _report_call(node, model_name, _request_text(state, questions), response)
    return _keyword_from_response(node, response)


def build_state(node: "RouterNode", context: "NodeContext") -> dict:
    """What Jev is asked about: the input, the history the node is configured to see, and the session's data."""
    history = node.get_history(exclude_message_id=context.input_message_id) if context.session else []
    return {
        "input": context.input,
        "history": [_history_entry(message) for message in history[-HISTORY_LIMIT:]],
        "participant_data": context.state.participant_data or {},
        "session_state": context.state.session_state or {},
        "temp_state": context.state.temp or {},
    }


def _history_entry(message: BaseMessage) -> dict:
    return {"role": message.type, "content": message.text[:MESSAGE_CHARACTER_LIMIT]}


def _criteria(node: "RouterNode") -> dict[str, str | None]:
    return {keyword: node.route_descriptions.get(keyword, "").strip() or None for keyword in node.keywords}


def _keyword_from_response(node: "RouterNode", response: JevResponse) -> str | None:
    answer = response.choice(QUESTION_NAME)
    if answer is None:
        logger.warning("Router %s got no answer from Jev", node.name)
        return None
    keyword = answer.choice.upper()
    if keyword not in node.keywords:
        logger.warning("Router %s got an unknown route from Jev: %s", node.name, answer.choice)
        return None
    if answer.confidence < node.confidence_threshold:
        logger.info(
            "Router %s took the default route: Jev chose %s with confidence %s, below %s",
            node.name,
            keyword,
            answer.confidence,
            node.confidence_threshold,
        )
        return None
    return keyword


def _request_text(state: Any, questions: dict[str, ChoiceQuestion]) -> str:
    return json.dumps({"state": state, "questions": {name: question.to_json() for name, question in questions.items()}})


def _report_call(node: "RouterNode", model_name: str, request_text: str, response: JevResponse) -> None:
    """Report the call to the run's callbacks as an LLM call, so tracers and cost tracking see its usage.

    Reported only once a response is in hand: a failed call has no usage to record, and the router
    recovers from it by taking the default route.
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
