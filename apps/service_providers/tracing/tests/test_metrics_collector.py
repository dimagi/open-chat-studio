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


class TestMetricsCollectorTiming:
    def test_first_and_last_token_timing(self):
        # Use an integer start_time to avoid floating-point precision issues
        start = 1000.0
        collector = MetricsCollector(start_time=start)

        collector._first_token_time = start + 0.1
        collector._first_token_recorded = True
        collector._last_token_time = start + 0.5

        metrics = collector.get_metrics()
        assert metrics.time_to_first_token == 100
        assert metrics.time_to_last_token == 500

    def test_on_llm_new_token_records_first_token(self):
        start = time.time()
        collector = MetricsCollector(start_time=start)

        collector.on_llm_new_token("Hello")
        first_time = collector._first_token_time
        assert first_time is not None

        collector.on_llm_new_token(" world")
        # First token time should not change
        assert collector._first_token_time == first_time
        # Last token time should be updated
        assert collector._last_token_time is not None
        assert collector._last_token_time >= first_time

    def test_no_streaming_returns_none(self):
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        collector.on_llm_end(LLMResult(generations=[], llm_output=None))

        metrics = collector.get_metrics()
        assert metrics.time_to_first_token is None
        assert metrics.time_to_last_token is None


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
        assert metrics.time_to_first_token is None
        assert metrics.time_to_last_token is None

    def test_turns_present_but_no_tokens(self):
        """LLM called but no token_usage reported — n_turns is set, n_total_tokens is None."""
        collector = MetricsCollector(start_time=time.time())
        collector.on_llm_start({}, ["prompt"])
        collector.on_llm_end(LLMResult(generations=[], llm_output=None))

        metrics = collector.get_metrics()
        assert metrics.n_turns == 1
        assert metrics.n_total_tokens is None
