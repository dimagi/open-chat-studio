import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseLanguageModel
from langchain_core.outputs import LLMResult


class TokenCountingCallbackHandler(BaseCallbackHandler):
    """Callback Handler that counts tokens using the LLM Model methods."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __init__(self, model: BaseLanguageModel):
        super().__init__()
        self._lock = threading.Lock()
        self.model = model

    def __repr__(self) -> str:
        return f"Prompt Tokens: {self.prompt_tokens}\n" f"Completion Tokens: {self.completion_tokens}\n"

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs) -> Any:
        with self._lock:
            self.prompt_tokens += sum([self.model.get_num_tokens(prompt) for prompt in prompts])

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Collect token usage."""
        messages = []
        for i, generations in enumerate(response.generations):
            for j, generation in enumerate(generations):
                messages.append(generation.message)

        with self._lock:
            self.completion_tokens += self.model.get_num_tokens_from_messages(messages)
