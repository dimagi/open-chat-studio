from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import UsageMetadataCallbackHandler

from apps.cost_tracking.models import Confidence, ServiceKind
from apps.cost_tracking.services.estimation import has_usage_metadata, response_text, tiktoken_count
from apps.cost_tracking.services.recorder import UsageEvent

# Providers we can tiktoken-estimate when usage_metadata is missing.
# Non-family providers fall through to the UNKNOWN bucket instead.
OPENAI_FAMILY = frozenset({"openai", "azure"})

# Some LangChain integrations (e.g. OpenAI flex/priority tiers) prefix the
# cache-read key in `input_token_details`. We sum across all aliases so
# those calls aren't silently classified as un-cached.
_CACHE_READ_KEYS = ("cache_read", "flex_cache_read", "priority_cache_read")
_CACHE_WRITE_KEYS = ("cache_creation",)


@dataclass
class TraceMetrics:
    """Metrics collected during a single pipeline execution."""

    n_turns: int | None = None
    n_toolcalls: int | None = None
    n_total_tokens: int | None = None
    n_prompt_tokens: int | None = None
    n_completion_tokens: int | None = None


class MetricsCollector(UsageMetadataCallbackHandler):
    """Thread-safe accumulator for pipeline execution metrics.

    Collects turn counts, tool call counts, and token usage across all LLM
    calls in a pipeline execution. Token usage is inherited from
    UsageMetadataCallbackHandler, which reads AIMessage.usage_metadata — the
    standard location populated by modern LangChain chat-model integrations
    (including OpenAI's Responses API, where LLMResult.llm_output is None).

    Counter access is protected by a threading lock because LangGraph
    executes nodes in threads via DjangoSafeContextThreadPoolExecutor.

    Cost tracking extension: captures provider slug per model from the
    `ocs_provider_type` metadata set by `LlmService.get_chat_model`, plus
    per-call pending state so `on_llm_end` can route missing-usage calls
    into a fallback bucket (tiktoken-estimated for OpenAI family, zero-
    quantity for everything else). `iter_cost_events()` drains both the
    exact `usage_metadata` and the fallback buckets into `UsageEvent`s.
    """

    def __init__(self, start_time: float):
        super().__init__()
        self._counter_lock = threading.Lock()
        self._turns = 0
        self._toolcalls = 0
        # Cost-tracking state. All access guarded by `_counter_lock`; the
        # parent's `_lock` covers `self.usage_metadata` separately.
        self._model_providers: dict[str, str] = {}
        self._pending_calls: dict[UUID, dict[str, Any]] = {}
        self._fallback_usage: dict[tuple[str, Confidence], dict[str, int]] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        with self._counter_lock:
            self._turns += 1
            meta = kwargs.get("metadata") or {}
            invocation = kwargs.get("invocation_params") or {}
            model = invocation.get("model") or meta.get("ls_model_name")
            provider = meta.get("ocs_provider_type")
            if model and provider:
                self._model_providers.setdefault(model, provider)
                # Per-call state only when the callback manager supplies a
                # run_id (production path). Tests bypassing the manager
                # still exercise turn counting + parent usage accumulation.
                if run_id is not None:
                    self._pending_calls[run_id] = {
                        "model": model,
                        "provider": provider,
                        # Prompts retained only for the tiktoken estimate path.
                        "prompts": prompts if provider in OPENAI_FAMILY else None,
                    }

    def on_llm_end(self, response, *, run_id: UUID | None = None, **kwargs: Any) -> None:
        # Parent populates self.usage_metadata for the EXACT path.
        super().on_llm_end(response, run_id=run_id, **kwargs)
        if run_id is None:
            return
        with self._counter_lock:
            pending = self._pending_calls.pop(run_id, None)
        if pending is None:
            return
        if has_usage_metadata(response):
            return
        if pending["provider"] in OPENAI_FAMILY and pending["prompts"]:
            input_tokens = tiktoken_count(pending["model"], pending["prompts"])
            output_tokens = tiktoken_count(pending["model"], response_text(response))
            self._fallback_add(pending["model"], Confidence.ESTIMATED, input_tokens, output_tokens)
        else:
            self._fallback_add(pending["model"], Confidence.UNKNOWN, 0, 0)

    def _fallback_add(self, model: str, confidence: Confidence, input_tokens: int, output_tokens: int) -> None:
        with self._counter_lock:
            bucket = self._fallback_usage.setdefault(
                (model, confidence),
                {"input_tokens": 0, "output_tokens": 0, "calls": 0},
            )
            bucket["input_tokens"] += input_tokens
            bucket["output_tokens"] += output_tokens
            bucket["calls"] += 1

    def iter_cost_events(self) -> Iterator[UsageEvent]:
        """Drain accumulated usage as UsageEvents for the cost recorder.

        Yields events from two sources:
          - `self.usage_metadata` (parent class) -> confidence=EXACT.
          - `self._fallback_usage` -> confidence=ESTIMATED or UNKNOWN.

        Zero-quantity buckets are skipped except for UNKNOWN, which yields
        a single zero-quantity LLM_INPUT row per (model, provider) so the
        weekly digest can surface coverage gaps.
        """
        with self._lock:
            exact_snapshot = dict(self.usage_metadata)
        with self._counter_lock:
            providers_snapshot = dict(self._model_providers)
            fallback_snapshot = dict(self._fallback_usage)

        yield from _iter_exact_events(exact_snapshot, providers_snapshot)
        yield from _iter_fallback_events(fallback_snapshot, providers_snapshot)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        with self._counter_lock:
            self._toolcalls += 1

    def get_metrics(self) -> TraceMetrics:
        """Return collected metrics, converting zero counts to None.

        Zero counts are converted to None to distinguish "no LLM calls happened"
        from "LLM calls happened but produced 0 tokens".
        """
        with self._counter_lock:
            turns = self._turns
            toolcalls = self._toolcalls
        with self._lock:
            prompt_tokens = sum(u.get("input_tokens", 0) for u in self.usage_metadata.values())
            completion_tokens = sum(u.get("output_tokens", 0) for u in self.usage_metadata.values())
            total_tokens = sum(u.get("total_tokens", 0) for u in self.usage_metadata.values())
        return TraceMetrics(
            n_turns=turns or None,
            n_toolcalls=toolcalls or None,
            n_total_tokens=total_tokens or None,
            n_prompt_tokens=prompt_tokens or None,
            n_completion_tokens=completion_tokens or None,
        )


def _iter_exact_events(
    exact_snapshot: dict[str, dict],
    providers_snapshot: dict[str, str],
) -> Iterator[UsageEvent]:
    """Yield EXACT events from the parent class's `usage_metadata` dict."""
    for model_name, usage in exact_snapshot.items():
        provider = providers_snapshot.get(model_name, "unknown")
        yield from _exact_events_for_model(model_name, provider, usage)


def _exact_events_for_model(model_name: str, provider: str, usage: dict) -> Iterator[UsageEvent]:
    for kind, qty in _split_buckets(usage):
        if qty:
            yield UsageEvent(
                service_kind=kind,
                provider_type=provider,
                model_name=model_name,
                quantity=qty,
                confidence=Confidence.EXACT,
            )


def _iter_fallback_events(
    fallback_snapshot: dict[tuple[str, Confidence], dict[str, int]],
    providers_snapshot: dict[str, str],
) -> Iterator[UsageEvent]:
    """Yield ESTIMATED and UNKNOWN events from the missing-usage fallback buckets."""
    for (model_name, confidence), bucket in fallback_snapshot.items():
        provider = providers_snapshot.get(model_name, "unknown")
        if confidence is Confidence.UNKNOWN:
            yield _unknown_event(model_name, provider, bucket)
        else:
            yield from _estimated_events(model_name, provider, bucket)


def _unknown_event(model_name: str, provider: str, bucket: dict[str, int]) -> UsageEvent:
    return UsageEvent(
        service_kind=ServiceKind.LLM_INPUT,
        provider_type=provider,
        model_name=model_name,
        quantity=0,
        confidence=Confidence.UNKNOWN,
        extra={"missing_usage_calls": bucket["calls"]},
    )


def _estimated_events(model_name: str, provider: str, bucket: dict[str, int]) -> Iterator[UsageEvent]:
    for kind, qty in (
        (ServiceKind.LLM_INPUT, bucket["input_tokens"]),
        (ServiceKind.LLM_OUTPUT, bucket["output_tokens"]),
    ):
        if qty:
            yield UsageEvent(
                service_kind=kind,
                provider_type=provider,
                model_name=model_name,
                quantity=qty,
                confidence=Confidence.ESTIMATED,
                extra={"estimator": "tiktoken"},
            )


def _sum_keys(details: dict, keys: tuple[str, ...]) -> int:
    return sum((details.get(k) or 0) for k in keys)


def _split_buckets(usage: dict) -> list[tuple[ServiceKind, int]]:
    """Split a `usage_metadata` dict into non-overlapping per-ServiceKind buckets.

    LangChain's headline `input_tokens` already includes the nested
    `input_token_details` sub-buckets (cache_read, cache_creation), so we
    subtract them to avoid double-counting. Reasoning tokens stay folded
    into `output_tokens` — no provider bills them at a separate rate.
    `max(0, ...)` is a defensive clamp against a future integration that
    breaks the "headline includes sub-buckets" convention.
    """
    in_details = usage.get("input_token_details") or {}
    cache_read = _sum_keys(in_details, _CACHE_READ_KEYS)
    cache_write = _sum_keys(in_details, _CACHE_WRITE_KEYS)
    input_net = max(0, usage.get("input_tokens", 0) - cache_read - cache_write)
    output_net = usage.get("output_tokens", 0)
    return [
        (ServiceKind.LLM_INPUT, input_net),
        (ServiceKind.LLM_OUTPUT, output_net),
        (ServiceKind.LLM_CACHED_INPUT, cache_read),
        (ServiceKind.LLM_CACHE_WRITE, cache_write),
    ]
