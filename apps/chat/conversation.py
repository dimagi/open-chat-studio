import contextlib
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.prompts import (
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain_core.prompts.prompt import PromptTemplate

from apps.utils.prompt import OcsPromptTemplate

COMPRESSION_MARKER = "__compression_marker__"

SUMMARY_TOO_LARGE_ERROR_MESSAGE = "Unable to compress chat history: existing summary too large"
MESSAGES_TOO_LARGE_ERROR_MESSAGE = (
    "Unable to compress chat history: Messages are too large for the context window of {tokens} tokens"
)
INITIAL_SUMMARY_TOKENS_ESTIMATE = 20
# The maximum number of messages that can be uncompressed
MAX_UNCOMPRESSED_MESSAGES = 1000

_SUMMARY_COMPRESSION_TEMPLATE = """Compress this summary into a shorter summary, about half of its original size:
    SUMMARY:
    {summary}

    Shorter summary:
"""

SUMMARY_COMPRESSION_PROMPT = PromptTemplate.from_template(_SUMMARY_COMPRESSION_TEMPLATE)

log = logging.getLogger("ocs.bots")


class BasicConversation:
    """
    A wrapper class that provides a single way/API to interact with the LLMs, regardless of it being a normal
    conversation or agent implementation
    """

    def __init__(
        self,
        prompt_str: str,
        source_material: str,
        llm: BaseChatModel,
    ):
        self.prompt_str = prompt_str
        self.source_material = source_material
        self.llm = llm
        self._build_chain()

        self.messages = []

    def _build_chain(self):
        prompt = OcsPromptTemplate.from_messages(
            [
                self.system_prompt,
                MessagesPlaceholder(variable_name="history"),
                HumanMessagePromptTemplate.from_template("{input}"),
            ]
        )

        # set output_key to match agent's output_key
        self.chain = prompt | self.llm

    @property
    def system_prompt(self):
        prompt_to_use = SystemMessagePromptTemplate.from_template(self.prompt_str)
        if self.source_material:
            with contextlib.suppress(KeyError):
                prompt_to_use = prompt_to_use.format(source_material=self.source_material)
        return prompt_to_use

    def load_memory_from_messages(self, messages: list[BaseMessage]):
        self.messages = messages

    def predict(self, input: str) -> tuple[str, int, int]:
        response = self.chain.invoke({"input": input, "history": self.messages})
        usage = response.usage_metadata or {}
        return response.text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
