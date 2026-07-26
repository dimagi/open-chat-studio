import threading
import time
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.cost_tracking.models import Confidence, ServiceKind
from apps.service_providers.tracing.metrics import MetricsCollector, _split_buckets


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


# Cost-tracking extension: provider capture, on_llm_end fallback, iter_cost_events


def _start_args(*, model: str, provider: str | None, prompts: list[str] | None = None) -> dict:
    """kwargs shape that the real LangChain callback manager passes to on_llm_start."""
    meta = {"ocs_provider_type": provider} if provider else {}
    return {
        "prompts": ["prompt"] if prompts is None else prompts,
        "run_id": uuid4(),
        "invocation_params": {"model": model},
        "metadata": meta,
    }


def _result_with_usage(model: str, input_tokens: int, output_tokens: int) -> LLMResult:
    return _make_llm_result(input_tokens=input_tokens, output_tokens=output_tokens, model_name=model)


def _result_without_usage(text: str = "response") -> LLMResult:
    """Chat result whose AIMessage has `usage_metadata=None`."""
    message = AIMessage(content=text, usage_metadata=None)
    return LLMResult(generations=[[ChatGeneration(message=message, text=text)]], llm_output=None)


class TestProviderCapture:
    """`on_llm_start` captures (provider, model) into per-call pending state."""

    def test_pending_call_records_provider_and_model(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai")
        collector.on_llm_start({}, **args)
        pending = collector._pending_calls[args["run_id"]]
        assert pending["provider"] == "openai"
        assert pending["model"] == "gpt-4o-mini"

    def test_missing_metadata_is_silently_ignored(self):
        collector = MetricsCollector(start_time=time.time())
        # No metadata kwarg — turn-counting still works; nothing captured.
        collector.on_llm_start({}, ["prompt"])
        assert collector._pending_calls == {}

    def test_prompts_kept_for_all_providers(self):
        """Both OPENAI_FAMILY and other providers retain prompts so the
        estimate path can count tokens (tiktoken or approximate)."""
        collector = MetricsCollector(start_time=time.time())
        openai_args = _start_args(model="gpt-4o-mini", provider="openai", prompts=["hi"])
        anthropic_args = _start_args(model="claude-haiku-4-5", provider="anthropic", prompts=["hi"])
        collector.on_llm_start({}, **openai_args)
        collector.on_llm_start({}, **anthropic_args)
        assert collector._pending_calls[openai_args["run_id"]]["prompts"] == ["hi"]
        assert collector._pending_calls[anthropic_args["run_id"]]["prompts"] == ["hi"]


class TestOnLlmEndFallback:
    """`on_llm_end` routes missing-usage calls into the fallback bucket."""

    def test_exact_path_does_not_populate_fallback(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai")
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_with_usage("gpt-4o-mini", 100, 50), run_id=args["run_id"])
        assert collector._fallback_usage == {}
        # Parent class accumulated usage_metadata.
        assert sum(u["input_tokens"] for u in collector.usage_metadata.values()) == 100

    def test_pending_call_cleared_on_end(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai")
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_with_usage("gpt-4o-mini", 100, 50), run_id=args["run_id"])
        assert collector._pending_calls == {}

    def test_openai_missing_usage_routes_to_estimated(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai", prompts=["hello world"])
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_without_usage("a response"), run_id=args["run_id"])
        bucket = collector._fallback_usage[("openai", "gpt-4o-mini", Confidence.ESTIMATED)]
        assert bucket["input_tokens"] > 0  # tiktoken counted the prompt
        assert bucket["output_tokens"] > 0  # tiktoken counted the response
        assert bucket["calls"] == 1

    @pytest.mark.parametrize(
        ("prompts", "expected_confidence", "qty_positive"),
        [
            pytest.param(["hello"], Confidence.ESTIMATED, True, id="with-prompts→estimated"),
            pytest.param([], Confidence.UNKNOWN, False, id="no-prompts→unknown"),
        ],
    )
    def test_non_openai_fallback_routing(self, prompts, expected_confidence, qty_positive):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="claude-haiku-4-5", provider="anthropic", prompts=prompts)
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_without_usage("a response"), run_id=args["run_id"])
        bucket = collector._fallback_usage[("anthropic", "claude-haiku-4-5", expected_confidence)]
        if qty_positive:
            assert bucket["input_tokens"] > 0
            assert bucket["output_tokens"] > 0
        else:
            assert bucket["input_tokens"] == 0
            assert bucket["output_tokens"] == 0
        assert bucket["calls"] == 1

    def test_unknown_calls_accumulate(self):
        """Two no-prompts calls to the same model count as 2 in `missing_usage_calls`."""
        collector = MetricsCollector(start_time=time.time())
        for _ in range(2):
            args = _start_args(model="claude-haiku-4-5", provider="anthropic", prompts=[])
            collector.on_llm_start({}, **args)
            collector.on_llm_end(_result_without_usage(), run_id=args["run_id"])
        bucket = collector._fallback_usage[("anthropic", "claude-haiku-4-5", Confidence.UNKNOWN)]
        assert bucket["calls"] == 2

    def test_same_model_distinct_providers_kept_separate(self):
        """Same model name routed through two providers must bucket separately
        — otherwise usage gets billed to whichever provider was seen first.
        Both openai and azure are in OPENAI_FAMILY so this exercises ESTIMATED.
        """
        collector = MetricsCollector(start_time=time.time())
        for provider in ("openai", "azure"):
            args = _start_args(model="gpt-4o-mini", provider=provider, prompts=["hi"])
            collector.on_llm_start({}, **args)
            collector.on_llm_end(_result_without_usage(), run_id=args["run_id"])
        assert ("openai", "gpt-4o-mini", Confidence.ESTIMATED) in collector._fallback_usage
        assert ("azure", "gpt-4o-mini", Confidence.ESTIMATED) in collector._fallback_usage


class TestIterCostEvents:
    """`iter_cost_events` drains usage_metadata + fallback buckets into UsageEvents."""

    def test_empty_collector_yields_nothing(self):
        collector = MetricsCollector(start_time=time.time())
        assert list(collector.iter_cost_events()) == []

    def test_exact_path_yields_input_output_events(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai")
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_with_usage("gpt-4o-mini", 100, 50), run_id=args["run_id"])

        events = list(collector.iter_cost_events())
        kinds = {(e.service_kind, e.quantity) for e in events}
        assert (ServiceKind.LLM_INPUT, 100) in kinds
        assert (ServiceKind.LLM_OUTPUT, 50) in kinds
        # All events tagged with the OCS provider slug from metadata.
        assert all(e.provider_type == "openai" for e in events)
        assert all(e.confidence is Confidence.EXACT for e in events)

    def test_estimated_path_yields_tiktoken_events(self):
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="gpt-4o-mini", provider="openai", prompts=["hello world"])
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_without_usage("a response"), run_id=args["run_id"])

        events = list(collector.iter_cost_events())
        assert all(e.confidence is Confidence.ESTIMATED for e in events)
        assert all(e.extra == {"estimator": "tiktoken"} for e in events)
        assert all(e.quantity > 0 for e in events)

    def test_unknown_path_yields_zero_quantity_row(self):
        """UNKNOWN bucket (no prompts AND no usage_metadata) emits a single
        zero-quantity row per (provider, model) so the digest can flag it."""
        collector = MetricsCollector(start_time=time.time())
        for _ in range(2):
            args = _start_args(model="claude-haiku-4-5", provider="anthropic", prompts=[])
            collector.on_llm_start({}, **args)
            collector.on_llm_end(_result_without_usage(), run_id=args["run_id"])

        events = list(collector.iter_cost_events())
        unknown = [e for e in events if e.confidence is Confidence.UNKNOWN]
        assert len(unknown) == 1
        assert unknown[0].quantity == 0
        assert unknown[0].extra == {"missing_usage_calls": 2}

    def test_non_openai_estimated_uses_approximate_marker(self):
        """Non-OPENAI ESTIMATED events carry estimator=approximate so the
        digest can distinguish tiktoken-accurate from heuristic counts."""
        collector = MetricsCollector(start_time=time.time())
        args = _start_args(model="claude-haiku-4-5", provider="anthropic", prompts=["hello"])
        collector.on_llm_start({}, **args)
        collector.on_llm_end(_result_without_usage("a response"), run_id=args["run_id"])

        events = list(collector.iter_cost_events())
        assert all(e.confidence is Confidence.ESTIMATED for e in events)
        assert all(e.extra == {"estimator": "approximate"} for e in events)
        assert all(e.quantity > 0 for e in events)

    def test_exact_path_keys_by_provider_and_model(self):
        """Same model name through two providers yields one exact event per provider."""
        collector = MetricsCollector(start_time=time.time())
        for provider in ("openai", "azure"):
            args = _start_args(model="gpt-4o-mini", provider=provider)
            collector.on_llm_start({}, **args)
            collector.on_llm_end(_result_with_usage("gpt-4o-mini", 100, 50), run_id=args["run_id"])

        events = list(collector.iter_cost_events())
        providers = {(e.provider_type, e.model_name, e.service_kind) for e in events}
        assert ("openai", "gpt-4o-mini", ServiceKind.LLM_INPUT) in providers
        assert ("azure", "gpt-4o-mini", ServiceKind.LLM_INPUT) in providers


class TestSplitBuckets:
    """`_split_buckets`: non-overlapping subtraction of cache sub-buckets."""

    def test_plain_usage_returns_input_and_output(self):
        buckets = dict(_split_buckets({"input_tokens": 100, "output_tokens": 50}))
        assert buckets[ServiceKind.LLM_INPUT] == 100
        assert buckets[ServiceKind.LLM_OUTPUT] == 50
        assert buckets[ServiceKind.LLM_CACHED_INPUT] == 0
        assert buckets[ServiceKind.LLM_CACHE_WRITE] == 0

    @pytest.mark.parametrize(
        ("detail_key", "detail_qty", "expected_bucket"),
        [
            pytest.param("cache_read", 30, ServiceKind.LLM_CACHED_INPUT, id="openai-cache-read"),
            pytest.param("cache_creation", 40, ServiceKind.LLM_CACHE_WRITE, id="anthropic-cache-write"),
            pytest.param("flex_cache_read", 30, ServiceKind.LLM_CACHED_INPUT, id="openai-flex-tier-alias"),
        ],
    )
    def test_cache_subtype_subtracted_from_input(self, detail_key, detail_qty, expected_bucket):
        """The cache sub-bucket is subtracted from headline input (avoiding
        double-count) and lands in its own ServiceKind bucket."""
        buckets = dict(
            _split_buckets(
                {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "input_token_details": {detail_key: detail_qty},
                }
            )
        )
        assert buckets[ServiceKind.LLM_INPUT] == 100 - detail_qty
        assert buckets[expected_bucket] == detail_qty

    def test_negative_net_clamped_to_zero(self):
        """If a future integration over-reports sub-buckets, never produce a
        negative bucket (which would yield a negative cost downstream)."""
        buckets = dict(
            _split_buckets(
                {
                    "input_tokens": 10,
                    "output_tokens": 50,
                    "input_token_details": {"cache_read": 30},  # exceeds the headline
                }
            )
        )
        assert buckets[ServiceKind.LLM_INPUT] == 0
