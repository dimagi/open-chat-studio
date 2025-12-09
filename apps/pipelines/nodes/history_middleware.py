from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import BaseMessage, RemoveMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import (
    REMOVE_ALL_MESSAGES,
)

from apps.chat.conversation import COMPRESSION_MARKER
from apps.chat.models import ChatMessage
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatMessages

if TYPE_CHECKING:
    from apps.pipelines.nodes.nodes import PipelineNode

logger = logging.getLogger("ocs.bots.summarization")


class HistoryCompressionMiddleware(SummarizationMiddleware):
    """
    Middleware to summarize chat history based on node configuration.

    History is loaded into the state in the before_agent step, and summaries are persisted
    to the database in the before_model step, if a summary is generated.
    """

    def __init__(self, session, node: PipelineNode, **kwargs):
        super().__init__(model=node.get_chat_model(), **kwargs)
        self.session = session
        self.node = node

    def before_agent(self, state, runtime):
        return {
            "messages": [
                # Since this response will get merged with the existing state messages, we cannot simply append the
                # history to the user's message. We need to replace the full message history in the state.
                # See https://github.com/langchain-ai/langchain/blob/c63f23d2339b2604edc9ae1d9f7faf7d6cc7dc78/libs/langchain_v1/langchain/agents/middleware/summarization.py#L286-L292
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self.node.get_history(self.session),
                *state["messages"],
            ]
        }

    def before_model(self, state, runtime):
        result = super().before_model(state, runtime)
        if result is not None:
            self._persist_summary(result["messages"])

        return result

    def _create_summary(self, messages_to_summarize: list) -> str:
        """
        Create summary based on node's history mode
        TRUNCATE_TOKENS and MAX_HISTORY_LENGTH mode does not create summaries, it only removes old messages until under
        token or message limit.
        """
        if self.node.history_mode == PipelineChatHistoryModes.TRUNCATE_TOKENS:
            return COMPRESSION_MARKER
        elif self.node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
            # We don't bother creating a summary
            return ""
        return super()._create_summary(messages_to_summarize)

    def _build_new_messages(self, summary: str) -> list:
        if self.node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
            # Don't include a summary message
            return []

        return super()._build_new_messages(summary)

    def _persist_summary(self, messages: list[BaseMessage]):
        history_mode = self.node.get_history_mode()

        checkpoint_message_id = self._find_latest_message_db_id(messages)
        if not checkpoint_message_id:
            # This should not happen, so we log it as an exception to surface it if it does
            logger.exception(
                "Unable to persist summary", extra={"node_id": self.node.node_id, "history_mode": history_mode}
            )
            return

        # The first message is always a RemoveMessage if a summary was created
        summary_message = messages[1]
        summary = summary_message.content

        if self.node.use_session_history():
            if summary == COMPRESSION_MARKER:
                metadata = {"compression_marker": history_mode}
            else:
                metadata = {"summary": summary}
            message = ChatMessage.objects.get(id=checkpoint_message_id)
            message.metadata.update(metadata)
            message.save(update_fields=["metadata"])
        else:
            # Use pipeline history
            updates = {"compression_marker": history_mode}
            if summary != COMPRESSION_MARKER:
                updates["summary"] = summary
            PipelineChatMessages.objects.filter(id=checkpoint_message_id).update(**updates)

    def _find_latest_message_db_id(self, messages: list) -> str | None:
        """
        Find the database ID of the latest message in the list that has one.
        Typically it will be at index -2, since the one at -1 is the new user message.
        """

        for i in range(len(messages) - 2, -1, -1):
            if "id" in messages[i].additional_kwargs:
                return messages[i].additional_kwargs["id"]


def get_history_compression_middleware(node, session, system_message) -> HistoryCompressionMiddleware | None:
    """
    Return the history compression middleware configured for this node.

    If ``history_type`` is ``NONE`` no middleware is attached; all other modes require
    compression. The ``history_mode`` determines the ``trigger`` and ``keep`` thresholds
    used to summarize the conversation history.

    Accounting for the system message:
    Since the system message is also takes up tokens in the context window, we need to
    subtract its token count from the total token limit when calculating the thresholds.
    """
    from apps.pipelines.nodes.nodes import get_llm_provider_model

    # TODO: Use the token counter from the LLM service
    system_message_tokens = count_tokens_approximately([system_message])

    if node.history_is_disabled():
        return None

    if node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        trigger = ("messages", 2)  # make this a low number to always trigger
        keep = ("messages", node.max_history_length)
    else:
        specified_token_limit = (
            node.user_max_token_limit
            if node.user_max_token_limit is not None
            else get_llm_provider_model(node.llm_provider_model_id).max_token_limit
        )
        # There must be at least 100 tokens to work with after accounting for system message. This number was chosen
        # somewhat arbitrarily to ensure there's enough room.
        token_limit = max(specified_token_limit - system_message_tokens, 100)

        trigger = ("tokens", token_limit)
        if node.history_mode == PipelineChatHistoryModes.SUMMARIZE:
            keep = ("messages", 20)
        else:
            keep = ("tokens", token_limit)

    return HistoryCompressionMiddleware(
        session=session, node=node, trigger=trigger, keep=keep, system_message=system_message
    )
