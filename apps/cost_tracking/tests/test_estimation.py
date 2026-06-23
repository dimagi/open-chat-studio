"""Unit tests for the missing-usage estimation helpers."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from apps.cost_tracking.services.estimation import (
    has_usage_metadata,
    response_text,
    tiktoken_count,
)


def _chat_result(text: str = "hello", *, usage: dict | None = None) -> LLMResult:
    """A ChatGeneration-shaped LLMResult, like a modern chat model emits."""
    message = AIMessage(content=text, usage_metadata=usage)
    return LLMResult(generations=[[ChatGeneration(message=message, text=text)]], llm_output=None)


def _text_result(text: str = "hello") -> LLMResult:
    """A plain Generation (text-completion shape) — no `.message` attribute."""
    return LLMResult(generations=[[Generation(text=text)]], llm_output=None)


# tiktoken_count


class TestTiktokenCount:
    """`tiktoken_count`: known/unknown models, list/str/None inputs."""

    def test_known_model_returns_positive_count(self):
        assert tiktoken_count("gpt-4o-mini", "hello world") > 0

    def test_unknown_model_falls_back_to_cl100k(self):
        assert tiktoken_count("ghost-model-9000", "hello world") > 0

    def test_list_input_sums_across_strings(self):
        single = tiktoken_count("gpt-4o-mini", "hello")
        listed = tiktoken_count("gpt-4o-mini", ["hello", "hello", "hello"])
        assert listed == 3 * single

    def test_empty_string_is_zero_tokens(self):
        assert tiktoken_count("gpt-4o-mini", "") == 0

    def test_none_is_zero_tokens(self):
        assert tiktoken_count("gpt-4o-mini", None) == 0

    def test_list_with_empty_strings_skips_them(self):
        with_blanks = tiktoken_count("gpt-4o-mini", ["hello", "", "world"])
        without = tiktoken_count("gpt-4o-mini", ["hello", "world"])
        assert with_blanks == without


# has_usage_metadata


class TestHasUsageMetadata:
    """`has_usage_metadata`: chat vs text-completion responses, mixed batches."""

    def test_true_when_chat_message_has_usage(self):
        result = _chat_result(usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8})
        assert has_usage_metadata(result) is True

    def test_false_when_chat_message_usage_is_none(self):
        result = _chat_result(usage=None)
        assert has_usage_metadata(result) is False

    def test_false_for_text_completion_responses(self):
        # Plain Generation has no `.message` — getattr defaults to None.
        result = _text_result()
        assert has_usage_metadata(result) is False

    def test_true_when_any_generation_has_usage(self):
        with_usage = AIMessage(content="a", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        without = AIMessage(content="b", usage_metadata=None)
        result = LLMResult(
            generations=[
                [ChatGeneration(message=without, text="b")],
                [ChatGeneration(message=with_usage, text="a")],
            ],
            llm_output=None,
        )
        assert has_usage_metadata(result) is True


# response_text


class TestResponseText:
    """`response_text`: join text content across generations, skip empties."""

    def test_joins_text_across_generations(self):
        result = LLMResult(
            generations=[
                [ChatGeneration(message=AIMessage(content="hello"), text="hello")],
                [ChatGeneration(message=AIMessage(content="world"), text="world")],
            ],
            llm_output=None,
        )
        assert response_text(result) == "hello world"

    def test_empty_string_when_no_text(self):
        result = LLMResult(
            generations=[[ChatGeneration(message=AIMessage(content=""), text="")]],
            llm_output=None,
        )
        assert response_text(result) == ""

    def test_skips_generations_with_empty_text(self):
        result = LLMResult(
            generations=[
                [ChatGeneration(message=AIMessage(content="a"), text="a")],
                [ChatGeneration(message=AIMessage(content=""), text="")],
                [ChatGeneration(message=AIMessage(content="b"), text="b")],
            ],
            llm_output=None,
        )
        assert response_text(result) == "a b"
