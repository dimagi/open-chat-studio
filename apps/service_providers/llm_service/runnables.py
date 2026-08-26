from __future__ import annotations

import logging
import re
from typing import Literal

from langchain_classic.agents.output_parsers import tools as lc_tools_parser
from langchain_core.load import Serializable

from apps.service_providers.llm_service.parsers import custom_parse_ai_message

lc_tools_parser.parse_ai_message_to_tool_action = custom_parse_ai_message  # ty: ignore[invalid-assignment]

logger = logging.getLogger("ocs.runnables")

# Matches a markdown link `[text](url)`, skipping footnote refs like `[1]`. Each character class
# excludes both its delimiters and is possessive (`++`), so a failed match backtracks in constant
# time and the whole scan stays linear even on adversarial input (ReDoS-safe). The example.com
# check happens in the callback rather than inside the pattern to keep the classes unambiguous.
_MARKDOWN_LINK_RE = re.compile(r"\[(?!\d+\])([^\[\]]++)\]\(([^()]++)\)")


def _strip_example_com_links(text: str) -> str:
    """Replace `[label](…example.com…)` links with just the emphasised label."""

    def replace(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        return f"*{label}*" if "example.com" in url else match.group(0)

    return _MARKDOWN_LINK_RE.sub(replace, text)


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
