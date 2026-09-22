"""Classify what a model turn came back as, from the signals each provider adapter exposes.

The signal lives in a different place per adapter: ``response_metadata["stop_reason"]``
(Anthropic), ``["finish_reason"]`` (OpenAI Chat Completions, Azure, DeepSeek, Gemini,
Vertex), ``["status"]`` plus ``["incomplete_details"]`` (OpenAI Responses API, which has
no finish_reason key), ``["prompt_feedback"]["block_reason"]`` (Gemini prompt block, an
int on an otherwise bare message), ``["is_blocked"]`` (Vertex), and a refusal either in
``additional_kwargs`` (Chat Completions) or as a content block (Responses API).
"""

from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import AIMessage

from apps.chat.exceptions import EmptyModelResponseError, ModelRefusedTurnError, ProviderConfigurationError
from apps.service_providers.llm_service.error_classification import TOKEN_LIMIT_MESSAGE

OutcomeKind = Literal["answered", "refusal", "content_filter", "length", "empty"]

GOOGLE_FILTER_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"})
LENGTH_REASONS = frozenset({"length", "MAX_TOKENS", "max_tokens", "model_context_window_exceeded", "max_output_tokens"})


@dataclass(frozen=True)
class TurnOutcome:
    """The classified kind of a model turn, its raw provider reason, and any filter detail."""

    kind: OutcomeKind
    provider_reason: str = ""
    detail: dict = field(default_factory=dict)


def classify_turn(message: AIMessage) -> TurnOutcome:
    """Classify a model turn's outcome kind and stop signal from its content and metadata."""
    metadata = message.response_metadata or {}
    reason = provider_reason(message)
    detail = _detail(metadata)
    if refusal_text(message):
        return TurnOutcome("refusal", "refusal", detail)
    kind = _outcome_kind(message, metadata, reason)
    if kind == "content_filter" and not reason:
        reason = "is_blocked"
    return TurnOutcome(kind, reason, detail)


def _outcome_kind(message: AIMessage, metadata: dict, reason: str) -> OutcomeKind:
    """Pick the outcome kind once a refusal carried in a content block has been ruled out."""
    if reason in ("refusal", "LANGUAGE"):
        return "refusal"
    if _is_content_filtered(metadata, reason):
        return "content_filter"
    if message.text:
        return "answered"
    if reason in LENGTH_REASONS:
        return "length"
    if message.tool_calls:
        return "answered"
    return "empty"


def _is_content_filtered(metadata: dict, reason: str) -> bool:
    """True when the provider's stop signal indicates a content filter block."""
    if reason == "content_filter" or reason in GOOGLE_FILTER_REASONS or _prompt_block_reason(metadata):
        return True
    return metadata.get("is_blocked") is True


def provider_reason(message: AIMessage) -> str:
    """The raw stop signal string the adapter exposed, or an empty string."""
    metadata = message.response_metadata or {}
    if reason := metadata.get("stop_reason") or metadata.get("finish_reason"):
        return str(reason)
    incomplete = metadata.get("incomplete_details") or {}
    if metadata.get("status") == "incomplete" and incomplete.get("reason"):
        return str(incomplete["reason"])
    if block_reason := _prompt_block_reason(metadata):
        return str(block_reason)
    if refusal_text(message):
        return "refusal"
    return ""


def refusal_text(message: AIMessage) -> str:
    """The refusal string carried in additional_kwargs or a refusal content block, or an empty string."""
    if refusal := message.additional_kwargs.get("refusal"):
        return refusal
    for block in message.content_blocks:
        # langchain-core wraps provider-specific blocks, which is how a Responses API refusal arrives
        value = block.get("value") if block.get("type") == "non_standard" else block
        if isinstance(value, dict) and value.get("type") == "refusal":
            return value.get("refusal", "")
    return ""


def _prompt_block_reason(metadata: dict) -> int | str | None:
    feedback = metadata.get("prompt_feedback") or {}
    return feedback.get("block_reason") or None


def _detail(metadata: dict) -> dict:
    detail = {}
    if ratings := metadata.get("safety_ratings"):
        detail["safety_ratings"] = ratings
    if stop_details := metadata.get("stop_details"):
        detail["stop_details"] = stop_details
    return detail


def raise_for_outcome(outcome: TurnOutcome, node_name: str) -> None:
    """Raise the tier-appropriate exception for anything but an answered turn."""
    if outcome.kind in ("refusal", "content_filter"):
        raise ModelRefusedTurnError(outcome.kind, outcome.provider_reason, outcome.detail)
    if outcome.kind == "length":
        raise ProviderConfigurationError(
            f"{TOKEN_LIMIT_MESSAGE} Node: {node_name}. Stop reason: {outcome.provider_reason}."
        )
    if outcome.kind == "empty":
        raise EmptyModelResponseError(outcome.provider_reason)
