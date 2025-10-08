# This file contains generic/reusable LLM service components used by both pipelines and experiments.
# Experiment-specific runnables (LLMChat classes, create_experiment_runnable) are in apps/experiments/runnables.py

# import the logger and required imports for assistant classes that are used by both pipelines and experiments
import logging
from typing import TYPE_CHECKING, Literal

from langchain_core.load import Serializable

logger = logging.getlogger("ocs.runnables")


if TYPE_CHECKING:
    pass


class ChainOutput(Serializable):
    """Generic output format for LLM chains. Used by both pipelines and experiments."""

    output: str
    """String text."""
    prompt_tokens: int
    """Number of tokens in the prompt."""
    completion_tokens: int
    """Number of tokens in the completion."""

    type: Literal["OcsChainOutput"] = "ChainOutput"

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """Return whether this class is serializable."""
        return True

    @classmethod
    def get_lc_namespace(cls) -> list[str]:
        """Get the namespace of the langchain object."""
        return ["ocs", "schema", "chain_output"]


class GenerationError(Exception):
    """Generic exception for LLM generation errors. Used by both pipelines and experiments."""

    pass


class GenerationCancelled(Exception):
    """Generic exception for cancelled LLM generations. Used by both pipelines and experiments."""

    def __init__(self, output: "ChainOutput"):
        self.output = output
