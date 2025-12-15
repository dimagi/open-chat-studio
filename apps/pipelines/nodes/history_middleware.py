from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import BaseMessage, RemoveMessage
from langgraph.graph.message import (
    REMOVE_ALL_MESSAGES,
)

from apps.chat.conversation import COMPRESSION_MARKER

if TYPE_CHECKING:
    from apps.pipelines.nodes.nodes import PipelineNode

logger = logging.getLogger("ocs.bots.summarization")


class BaseNodeHistoryMiddleware(SummarizationMiddleware):
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
            # A result means that a summary was created
            self.persist_summary(result["messages"])

        return result

    def persist_summary(self, messages: list[BaseMessage]):
        checkpoint_message_id = self._find_latest_message_db_id(messages)
        if not checkpoint_message_id:
            logger.error(
                "Unable to persist summary: no checkpoint message ID found",
                stack_info=True,
            )
            return

        # The first message is always a RemoveMessage if a summary was created

        self.node.store_compression_checkpoint(
            compression_marker=self._get_compression_marker(messages), checkpoint_message_id=checkpoint_message_id
        )

    def _find_latest_message_db_id(self, messages: list) -> str | None:
        """
        Find the database ID of the latest message in the list that has one.
        Typically it will be at index -2, since the one at -1 is the new user message.
        """

        for i in range(len(messages) - 2, -1, -1):
            if "id" in messages[i].additional_kwargs:
                return messages[i].additional_kwargs["id"]

    def _get_compression_marker(self, messages: list[BaseMessage]) -> str:
        return messages[1].content


class SummarizeHistoryMiddleware(BaseNodeHistoryMiddleware):
    """Summarizes overflowing history into a compact checkpoint."""

    def __init__(self, *args, token_limit: int, **kwargs):
        trigger = ("tokens", token_limit)
        keep = ("messages", 20)
        super().__init__(*args, trigger=trigger, keep=keep, **kwargs)


class TruncateTokensHistoryMiddleware(BaseNodeHistoryMiddleware):
    """Drops oldest messages whenever the token budget is exceeded."""

    def __init__(self, *args, token_limit: int, **kwargs):
        keep = trigger = ("tokens", token_limit)
        super().__init__(*args, trigger=trigger, keep=keep, **kwargs)

    def _create_summary(self, messages_to_summarize: list) -> str:
        # Instead of creating a summary, we'll persist a compression marker. See _build_new_messages
        return ""

    def _build_new_messages(self, summary: str) -> list:
        # No summary message should be injected into the state
        return []

    def _get_compression_marker(self, messages: list[BaseMessage]) -> str:
        """
        Returns a constant compression marker to indicate that messages were truncated.
        """
        return COMPRESSION_MARKER


class MaxHistoryLengthHistoryMiddleware(BaseNodeHistoryMiddleware):
    """Reduces history to a fixed number of recent messages without adding summaries."""

    def __init__(self, *args, max_history_length: int, **kwargs):
        trigger = ("messages", 2)  # keep trigger low so pruning always runs
        keep = ("messages", max_history_length)
        super().__init__(*args, trigger=trigger, keep=keep, **kwargs)

    def _create_summary(self, messages_to_summarize: list) -> str:
        # Returning an empty string ensures the parent middleware no-ops on summary creation
        return ""

    def _build_new_messages(self, summary: str) -> list:
        # No summary message should be injected for message-count-based pruning
        return []

    def persist_summary(self, messages: list[BaseMessage]):
        # No summary to persist for message-count-based pruning
        pass
