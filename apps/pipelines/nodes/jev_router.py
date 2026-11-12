"""Route an LLM Router node with one Jev Choice question instead of an LLM agent."""

import logging
from typing import TYPE_CHECKING

from apps.pipelines.nodes.jev_calls import ask_jev
from apps.service_providers.llm_service.jev import ChoiceQuestion, JevClient, JevError, JevResponse

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.nodes.nodes import RouterNode

logger = logging.getLogger("ocs.pipelines.nodes")

QUESTION_NAME = "route"


def route_with_jev(node: "RouterNode", context: "NodeContext", instructions: str, client: JevClient) -> str | None:
    """The keyword Jev chose, or None when the router should take its default route."""
    questions = {QUESTION_NAME: ChoiceQuestion(criteria=_criteria(node), instructions=instructions)}
    try:
        response = ask_jev(node, context, questions, client)
    except JevError as e:
        logger.warning("Router %s got no route from Jev: %s", node.name, e)
        return None
    return _keyword_from_response(node, response)


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
