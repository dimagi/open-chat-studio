import threading
import weakref
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseLanguageModel
from langchain_core.outputs import LLMResult

from apps.accounting.models import UsageType
from apps.accounting.usage import BaseUsageRecorder


class TokenCountingCallbackHandler(BaseCallbackHandler):
    """Callback Handler that counts tokens using the LLM Model methods."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __init__(self, model: BaseLanguageModel):
        super().__init__()
        self._lock = threading.Lock()
        # Use weakref to avoid circular reference between callback handler and model
        self.model = weakref.proxy(model)

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
        self.on_llm_end(response)


class UsageCallbackHandler(BaseCallbackHandler):
    raise_error = True

    def __init__(
        self, usage_recorder: BaseUsageRecorder, token_counting_callback: BaseCallbackHandler, metadata: dict = None
    ):
        super().__init__()
        self.usage_recorder = usage_recorder
        assert hasattr(token_counting_callback, "prompt_tokens")
        assert hasattr(token_counting_callback, "completion_tokens")
        self.token_counting_callback = token_counting_callback
        self.metadata = metadata or {}

    def on_llm_start(self, *args, **kwargs) -> Any:
        self.token_counting_callback.on_llm_start(*args, **kwargs)

    def on_llm_end(self, *args, **kwargs) -> None:
        self.token_counting_callback.on_llm_end(*args, **kwargs)

        with self.usage_recorder.update_metadata(self.metadata):
            if prompt_tokens := getattr(self.token_counting_callback, "prompt_tokens", None):
                self.usage_recorder.record_usage(UsageType.INPUT_TOKENS, prompt_tokens)

            if completion_tokens := getattr(self.token_counting_callback, "completion_tokens", None):
                self.usage_recorder.record_usage(UsageType.OUTPUT_TOKENS, completion_tokens)
