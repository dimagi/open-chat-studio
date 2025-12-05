import dataclasses
import logging

import tiktoken
from anthropic import Anthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import get_buffer_string
from langchain_core.outputs import LLMResult

logger = logging.getLogger("ocs.runnables")


class TokenCounter:
    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        return None

    def get_tokens_from_text(self, text) -> int:
        raise NotImplementedError()

    def get_tokens_from_messages(self, messages) -> int:
        try:
            return sum([message.usage_metadata["output_tokens"] for message in messages])
        except Exception:  # noqa
            return sum([self.get_tokens_from_text(get_buffer_string([m])) for m in messages])


@dataclasses.dataclass
class OpenAITokenCounter(TokenCounter):
    model: str

    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        if response.llm_output is None:
            return None

        if "token_usage" not in response.llm_output:
            return None

        token_usage = response.llm_output["token_usage"]
        completion_tokens = token_usage.get("completion_tokens", 0)
        prompt_tokens = token_usage.get("prompt_tokens", 0)

        return prompt_tokens, completion_tokens

    def get_tokens_from_text(self, text) -> int:
        if not text:
            return 0
        encoding_model = self._get_encoding_model()
        return len(encoding_model.encode(text))

    def _get_encoding_model(self) -> tiktoken.Encoding:
        try:
            return tiktoken.encoding_for_model(self.model)
        except KeyError:
            # fallback to gpt-4 if the model is not available for encoding
            model = "gpt-4"
            return tiktoken.get_encoding(model)


class AnthropicTokenCounter(TokenCounter):
    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        if not response.generations:
            return None

        input_tokens = 0
        output_tokens = 0
        for generations in response.generations:
            for generation in generations:
                if usage := getattr(generation.message, "usage_metadata", None):
                    input_tokens += usage.get("input_tokens") or 0
                    output_tokens += usage.get("output_tokens") or 0

        return input_tokens, output_tokens

    def get_tokens_from_text(self, text) -> int:
        client = Anthropic(api_key=self.api_key)
        try:
            token_count_response = client.messages.count_tokens(
                model=self.model, messages=[{"role": "user", "content": text}]
            )
            return token_count_response.input_tokens
        except Exception as e:
            logger.error(f"Error counting tokens: {e}")
            return 0


@dataclasses.dataclass
class GeminiTokenCounter(TokenCounter):
    model: str
    google_api_key: str

    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        if response.llm_output is None:
            return None

        input_tokens = response.llm_output.get("input_tokens")
        output_tokens = response.llm_output.get("output_tokens")

        if input_tokens is None or output_tokens is None:
            return None

        return input_tokens, output_tokens

    def get_tokens_from_text(self, text: str) -> int:
        # not implemented for now until we're on the new python-genai library
        return 0


@dataclasses.dataclass
class GoogleVertexAITokenCounter(TokenCounter):
    chat_model: BaseChatModel

    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        # TODO
        return None

    def get_tokens_from_text(self, text) -> int:
        return self.chat_model.get_num_tokens(text)
