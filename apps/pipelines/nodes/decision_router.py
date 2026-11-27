"""Route an LLM Router node with one decision backend choice question instead of an LLM agent."""

import logging
from typing import TYPE_CHECKING

from apps.pipelines.nodes.decision_calls import ask_decisions
from apps.service_providers.llm_service.decision_backend import (
    ChoiceQuestion,
    DecisionBackend,
    DecisionError,
    DecisionResponse,
)

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.nodes.nodes import RouterNode

logger = logging.getLogger("ocs.pipelines.nodes")

QUESTION_NAME = "route"


def route_with_decisions(
    node: "RouterNode", context: "NodeContext", instructions: str, backend: DecisionBackend
) -> str | None:
    """The keyword the decision model chose, or None when the router should take its default route."""
    questions = {QUESTION_NAME: ChoiceQuestion(options=_options(node), instructions=instructions)}
    try:
        response = ask_decisions(node, context, questions, backend)
    except DecisionError as e:
        logger.warning("Router %s got no route from %s: %s", node.name, backend.name, e)
        return None
    return _keyword_from_response(node, backend, response)


def _options(node: "RouterNode") -> dict[str, str | None]:
    return {keyword: node.route_descriptions.get(keyword, "").strip() or None for keyword in node.keywords}


def _keyword_from_response(node: "RouterNode", backend: DecisionBackend, response: DecisionResponse) -> str | None:
    answer = response.choice(QUESTION_NAME)
    if answer is None:
        logger.warning("Router %s got no answer from %s", node.name, backend.name)
        return None
    keyword = answer.choice.upper()
    if keyword not in node.keywords:
        logger.warning("Router %s got an unknown route from %s: %s", node.name, backend.name, answer.choice)
        return None
    if answer.confidence < node.confidence_threshold:
        logger.info(
            "Router %s took the default route: %s chose %s with confidence %s, below %s",
            node.name,
            backend.name,
            keyword,
            answer.confidence,
            node.confidence_threshold,
        )
        return None
    return keyword
