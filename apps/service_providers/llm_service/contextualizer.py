import logging
from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger("ocs.contextualizer")

# Anthropic's contextual retrieval prompt, adapted. The document is placed in
# the system prompt so it can be cached across chunks of the same file (prompt
# caching keys off system-message stability with most providers).
CONTEXT_SYSTEM_PROMPT = (
    "You situate a chunk of text within the document it came from. "
    "Below is the source document. For each chunk the user gives you, "
    "reply with a short, succinct context (a sentence or two) that "
    "explains what the chunk is about and how it relates to the "
    "overall document, so it can be found by search. Answer only with "
    "the context, nothing else.\n\n"
    "<document>\n{document}\n</document>"
)

CONTEXT_USER_PROMPT = (
    "Here is the chunk to situate within the overall document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Give the short context to situate this chunk within the document."
)

# Documents longer than this (in characters) are truncated before being sent
# to the contextualizer. Roughly 100k tokens at ~4 chars/token.
DEFAULT_MAX_DOCUMENT_CHARS = 400_000


class Contextualizer(metaclass=ABCMeta):
    """Generates a short context header that situates a chunk within its document.

    The header is stored on FileChunkEmbedding.context and prepended to the
    chunk before embedding and lexical indexing (see contextual retrieval,
    issue #2681).
    """

    @abstractmethod
    def get_context(self, *, document: str, chunk: str) -> str:
        """Return a short context string for the chunk, or "" if none could be produced."""


class StaticContextualizer(Contextualizer):
    """Zero-cost contextualizer that builds a header from document structure.

    Used as the fallback when the LLM contextualizer fails. Takes no external calls.
    """

    def __init__(self, *, file_name: str = "", page_number: int | None = None):
        self._file_name = file_name
        self._page_number = page_number

    def get_context(self, *, document: str, chunk: str) -> str:
        parts = []
        if self._file_name:
            parts.append(f"Source document: {self._file_name}.")
        if self._page_number:
            parts.append(f"Page {self._page_number}.")
        return " ".join(parts)


class LLMContextualizer(Contextualizer):
    """LLM-backed contextualizer.

    Calls the configured chat model with the document (in the system prompt,
    for prompt caching across chunks of the same file) and the chunk. On any
    failure it falls back to `fallback` so that indexing never fails because
    contextualization failed.
    """

    def __init__(
        self,
        chat_model: "BaseChatModel",
        *,
        fallback: Contextualizer | None = None,
        max_document_chars: int = DEFAULT_MAX_DOCUMENT_CHARS,
    ):
        self._chat_model = chat_model
        self._fallback = fallback or StaticContextualizer()
        self._max_document_chars = max_document_chars

    def get_context(self, *, document: str, chunk: str) -> str:
        truncated = document[: self._max_document_chars]
        messages = [
            ("system", CONTEXT_SYSTEM_PROMPT.format(document=truncated)),
            ("human", CONTEXT_USER_PROMPT.format(chunk=chunk)),
        ]
        try:
            response = self._chat_model.invoke(messages)
        except Exception as e:
            logger.warning(
                "LLM contextualization failed; falling back to static context",
                extra={"error": str(e)},
            )
            return self._fallback.get_context(document=document, chunk=chunk)
        return response.text.strip()