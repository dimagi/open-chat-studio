import threading
import time

from langchain_core.outputs import LLMResult

from apps.service_providers.tracing.metrics import MetricsCollector


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
    def _make_llm_result(self, prompt_tokens: int, completion_tokens: int) -> LLMResult:
        return LLMResult(
            generations=[],
            llm_output={"token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}},
        )

    def test_accumulates_tokens_across_calls(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_end(self._make_llm_result(100, 50))
        collector.on_llm_end(self._make_llm_result(200, 100))

        metrics = collector.get_metrics()
        assert metrics.n_total_tokens == 450

    def test_missing_llm_output_handled(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        collector.on_llm_end(LLMResult(generations=[], llm_output=None))

        metrics = collector.get_metrics()
        assert metrics.n_turns == 1
        assert metrics.n_total_tokens is None

    def test_missing_token_usage_key_handled(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_end(LLMResult(generations=[], llm_output={"model_name": "gpt-4"}))

        metrics = collector.get_metrics()
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
        """LLM called but no token_usage reported — n_turns is set, n_total_tokens is None."""
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        collector.on_llm_end(LLMResult(generations=[], llm_output=None))

        metrics = collector.get_metrics()
        assert metrics.n_turns == 1
        assert metrics.n_total_tokens is None
