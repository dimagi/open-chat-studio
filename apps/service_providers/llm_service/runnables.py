from __future__ import annotations

import logging
from typing import Literal

from langchain_classic.agents.output_parsers import tools as lc_tools_parser
from langchain_core.load import Serializable

from apps.service_providers.llm_service.parsers import custom_parse_ai_message

lc_tools_parser.parse_ai_message_to_tool_action = custom_parse_ai_message  # ty: ignore[invalid-assignment]

logger = logging.getLogger("ocs.runnables")


class GenerationError(Exception):
    pass


class GenerationCancelled(Exception):
    def __init__(self, output: ChainOutput):
        self.output = output


class ChainOutput(Serializable):
    output: str
    """String text."""
    prompt_tokens: int
    """Number of tokens in the prompt."""
    completion_tokens: int
    """Number of tokens in the completion."""

    type: Literal["ChainOutput"] = "ChainOutput"

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """Return whether this class is serializable."""
        return True

    @classmethod
    def get_lc_namespace(cls) -> list[str]:
        """Get the namespace of the langchain object."""
        return ["ocs", "schema", "chain_output"]
