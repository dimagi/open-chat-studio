import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from apps.service_providers.llm_service.token_counters import TokenCounter


class TokenCountingCallbackHandler(BaseCallbackHandler):
    """Callback Handler that counts tokens using the LLM Model methods."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __init__(self, token_counter: TokenCounter):
        super().__init__()
        self._lock = threading.Lock()
        self.token_counter = token_counter
        self.prompts_by_run = {}

    def __repr__(self) -> str:
        return f"Prompt Tokens: {self.prompt_tokens}\n" f"Completion Tokens: {self.completion_tokens}\n"

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs) -> Any:
        self.prompts_by_run[run_id] = " ".join(prompts)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs) -> None:
        prompt = self.prompts_by_run.pop(run_id, None)
        if tokens := self.token_counter.get_tokens_from_response(response):
            input_tokens, output_tokens = tokens
        else:
            input_tokens = self.token_counter.get_tokens_from_text(prompt)

            messages = []
            for i, generations in enumerate(response.generations):
                for j, generation in enumerate(generations):
                    messages.append(generation.message)

            output_tokens = self.token_counter.get_tokens_from_messages(messages)

        with self._lock:
            self.prompt_tokens += input_tokens
            self.completion_tokens += output_tokens

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run when LLM errors and collect token usage."""
        response = kwargs.get("response", None)
        if not response:
            return
        self.on_llm_end(response, run_id=run_id)
