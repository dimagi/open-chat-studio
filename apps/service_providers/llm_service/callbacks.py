from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from apps.accounting.models import UsageType
from apps.accounting.usage import BaseUsageRecorder
from apps.service_providers.llm_service.token_counters import TokenCounter


class UsageCallbackHandler(BaseCallbackHandler):
    raise_error = True

    def __init__(self, usage_recorder: BaseUsageRecorder, token_counter: TokenCounter, metadata: dict = None):
        super().__init__()
        self.usage_recorder = usage_recorder
        self.metadata = metadata or {}
        self.token_counter = token_counter
        self.prompts_by_run = {}

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

        if not (input_tokens or output_tokens):
            return

        with self.usage_recorder.update_metadata(self.metadata):
            if input_tokens:
                self.usage_recorder.record_usage(UsageType.INPUT_TOKENS, input_tokens)

            if output_tokens:
                self.usage_recorder.record_usage(UsageType.OUTPUT_TOKENS, output_tokens)
