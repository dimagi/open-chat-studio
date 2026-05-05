import threading
import time

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.service_providers.tracing.metrics import MetricsCollector


def _make_llm_result(
    input_tokens: int,
    output_tokens: int,
    model_name: str = "gpt-4.1-mini",
) -> LLMResult:
    """Build an LLMResult shaped like a modern chat-model response.

    Token usage lives on AIMessage.usage_metadata; llm_output is None,
    matching what OpenAI's Responses API and Anthropic actually emit.
    """
    message = AIMessage(
        content="response text",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model_name},
    )
    return LLMResult(
        generations=[[ChatGeneration(message=message)]],
        llm_output=None,
    )


class TestMetricsCollectorTurns:
    def test_on_llm_start_increments_turns(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt 1"])
        collector.on_llm_start({}, ["prompt 2"])

        metrics = collector.get_metrics()
        assert metrics.n_turns == 2

    def test_no_llm_calls_returns_none(self):
        collector = MetricsCollector(start_time=time.time())
        metrics = collector.get_metrics()
        assert metrics.n_turns is None


class TestMetricsCollectorToolCalls:
    def test_on_tool_start_increments_toolcalls(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_tool_start({"name": "search"}, "query")
        collector.on_tool_start({"name": "calculator"}, "2+2")
        collector.on_tool_start({"name": "search"}, "another query")

        metrics = collector.get_metrics()
        assert metrics.n_toolcalls == 3

    def test_no_tool_calls_returns_none(self):
        collector = MetricsCollector(start_time=time.time())
        metrics = collector.get_metrics()
        assert metrics.n_toolcalls is None


class TestMetricsCollectorTokens:
    def test_accumulates_tokens_across_calls(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_end(_make_llm_result(100, 50))
        collector.on_llm_end(_make_llm_result(200, 100))

        metrics = collector.get_metrics()
        assert metrics.n_prompt_tokens == 300
        assert metrics.n_completion_tokens == 150
        assert metrics.n_total_tokens == 450

    def test_accumulates_tokens_across_models(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_end(_make_llm_result(100, 50, model_name="gpt-4.1-mini"))
        collector.on_llm_end(_make_llm_result(80, 40, model_name="claude-haiku-4-5"))

        metrics = collector.get_metrics()
        assert metrics.n_prompt_tokens == 180
        assert metrics.n_completion_tokens == 90
        assert metrics.n_total_tokens == 270

    def test_missing_usage_metadata_handled(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        message = AIMessage(content="response", response_metadata={"model_name": "gpt-4.1-mini"})
        collector.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

        metrics = collector.get_metrics()
        assert metrics.n_turns == 1
        assert metrics.n_total_tokens is None

    def test_no_llm_calls_tokens_none(self):
        collector = MetricsCollector(start_time=time.time())
        metrics = collector.get_metrics()
        assert metrics.n_total_tokens is None


class TestMetricsCollectorThreadSafety:
    def test_concurrent_increments(self):
        collector = MetricsCollector(start_time=time.time())
        num_threads = 10
        calls_per_thread = 100
        barrier = threading.Barrier(num_threads)

        def worker():
            barrier.wait()
            for _ in range(calls_per_thread):
                collector.on_llm_start({}, ["prompt"])
                collector.on_tool_start({}, "input")

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        metrics = collector.get_metrics()
        assert metrics.n_turns == num_threads * calls_per_thread
        assert metrics.n_toolcalls == num_threads * calls_per_thread


class TestMetricsCollectorZeroToNone:
    """Verify that zero counts are converted to None to distinguish
    'no LLM calls' from '0 tokens used'."""

    def test_all_zeros_become_none(self):
        collector = MetricsCollector(start_time=time.time())
        metrics = collector.get_metrics()
        assert metrics.n_turns is None
        assert metrics.n_toolcalls is None
        assert metrics.n_total_tokens is None

    def test_turns_present_but_no_tokens(self):
        """LLM called but no usage_metadata reported — n_turns is set, n_total_tokens is None."""
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        message = AIMessage(content="response", response_metadata={"model_name": "gpt-4.1-mini"})
        collector.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

        metrics = collector.get_metrics()
        assert metrics.n_turns == 1
        assert metrics.n_total_tokens is None
