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
FILTER_REASONS = frozenset({"content_filter", *GOOGLE_FILTER_REASONS})


@dataclass(frozen=True)
class TurnOutcome:
    """The classified kind of a model turn, its raw provider reason, and any filter detail."""

    kind: OutcomeKind
    provider_reason: str = ""
    detail: dict = field(default_factory=dict)


def classify_turn(message: AIMessage) -> TurnOutcome:
    """Classify a model turn's outcome kind and stop signal from its content and metadata."""
    metadata = message.response_metadata or {}
    detail = _detail(metadata)
    if refusal_text(message):
        return TurnOutcome("refusal", "refusal", detail)
    reason = provider_reason(message)
    kind = _outcome_kind(message, metadata, reason)
    return TurnOutcome(kind, _reason_for_kind(kind, reason), detail)


def _reason_for_kind(kind: OutcomeKind, reason: str) -> str:
    """Fall back to the Vertex-only is_blocked signal when a content filter carries no other reason."""
    if kind != "content_filter":
        return reason
    if reason:
        return reason
    return "is_blocked"


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
    if reason in FILTER_REASONS:
        return True
    if _prompt_block_reason(metadata):
        return True
    return metadata.get("is_blocked") is True


def provider_reason(message: AIMessage) -> str:
    """The raw stop signal string the adapter exposed, or an empty string."""
    metadata = message.response_metadata or {}
    return (
        _stop_signal(metadata)
        or _incomplete_reason(metadata)
        or _block_reason_signal(metadata)
        or _refusal_signal(message)
    )


def _stop_signal(metadata: dict) -> str:
    """Return the adapter's stop_reason or finish_reason, or an empty string."""
    reason = metadata.get("stop_reason") or metadata.get("finish_reason")
    if not reason:
        return ""
    return str(reason)


def _incomplete_reason(metadata: dict) -> str:
    """Return the Responses API incomplete reason, or an empty string when the turn is not incomplete."""
    if metadata.get("status") != "incomplete":
        return ""
    incomplete = metadata.get("incomplete_details") or {}
    reason = incomplete.get("reason")
    if not reason:
        return ""
    return str(reason)


def _block_reason_signal(metadata: dict) -> str:
    """Return Gemini's prompt block reason as a string, or an empty string when the prompt was not blocked."""
    block_reason = _prompt_block_reason(metadata)
    if not block_reason:
        return ""
    return str(block_reason)


def _refusal_signal(message: AIMessage) -> str:
    """Return "refusal" when the message carries refusal text, or an empty string."""
    if refusal_text(message):
        return "refusal"
    return ""


def refusal_text(message: AIMessage) -> str:
    """The refusal string carried in additional_kwargs or a refusal content block, or an empty string."""
    if refusal := message.additional_kwargs.get("refusal"):
        return refusal
    for block in message.content_blocks:
        if refusal := _refusal_block(block):
            return refusal
    return ""


def _refusal_block(block: object) -> str:
    """Return the refusal text in a content block, or an empty string when the block carries none."""
    if not isinstance(block, dict):
        return ""
    # langchain-core wraps provider-specific blocks, which is how a Responses API refusal arrives
    value = block.get("value") if block.get("type") == "non_standard" else block
    if isinstance(value, dict) and value.get("type") == "refusal":
        refusal = value.get("refusal", "")
        return refusal if isinstance(refusal, str) else ""
    return ""


def _prompt_block_reason(metadata: dict) -> int | str | None:
    """Return Gemini's prompt block reason from the metadata, or None if the prompt was not blocked."""
    feedback = metadata.get("prompt_feedback") or {}
    return feedback.get("block_reason") or None


def _detail(metadata: dict) -> dict:
    """Return the safety ratings and stop details found in the metadata, keyed by name and present only when set."""
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
