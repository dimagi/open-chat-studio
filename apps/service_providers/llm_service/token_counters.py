import dataclasses

import google.generativeai as genai
import tiktoken
from anthropic._tokenizers import sync_get_tokenizer
from google.generativeai import GenerativeModel
from langchain_core.messages import get_buffer_string
from langchain_core.outputs import LLMResult


class TokenCounter:
    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        return None

    def get_tokens_from_text(self, text) -> int:
        raise NotImplementedError()

    def get_tokens_from_messages(self, messages) -> int:
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
    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        if response.llm_output is None:
            return None

        if "usage" not in response.llm_output:
            return None

        token_usage = response.llm_output["usage"]
        output_tokens = token_usage.get("output_tokens", 0)
        input_tokens = token_usage.get("input_tokens", 0)

        return input_tokens, output_tokens

    def get_tokens_from_text(self, text) -> int:
        tokenizer = sync_get_tokenizer()
        encoded_text = tokenizer.encode(text)
        return len(encoded_text.ids)


@dataclasses.dataclass
class GeminiTokenCounter(TokenCounter):
    model: str
    google_api_key: str

    def __post_init__(self):
        if not self.google_api_key:
            raise ValueError("KEY not found!")

        self.client = genai
        self.model = GenerativeModel(self.model)

    def get_tokens_from_response(self, response: LLMResult) -> None | tuple[int, int]:
        if response.llm_output is None:
            return None

        input_tokens = response.llm_output.get("input_tokens")
        output_tokens = response.llm_output.get("output_tokens")

        if input_tokens is None or output_tokens is None:
            return None

        return input_tokens, output_tokens

    def get_tokens_from_text(self, text: str) -> int:
        if not text:
            return 0

        response = self.client.count_tokens(model=self.model, contents=text)
        return response.total_tokens

    def get_tokens_from_messages(self, messages) -> int:
        total_tokens = 0
        for message in messages:
            if isinstance(message, dict) and "content" in message:
                total_tokens += self.get_tokens_from_text(message["content"])
            elif isinstance(message, str):
                total_tokens += self.get_tokens_from_text(message)
            else:
                print("Warning: Unsupported message format.")
        return total_tokens
