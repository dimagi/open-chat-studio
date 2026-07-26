"""Helpers for the missing-usage fallback path in MetricsCollector.

Only invoked when a provider's response carries no `usage_metadata` and we
need to either tiktoken-estimate (OpenAI family) or record an unknown row.
"""

import tiktoken

_DEFAULT_ENCODING = "cl100k_base"


def tiktoken_count(model: str, text: str | list[str] | None) -> int:
    """Count tokens for `text` under `model`'s encoding. Falls back to
    `cl100k_base` for unknown models. Treats None / empty as 0 tokens.
    """
    if not text:
        return 0
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding(_DEFAULT_ENCODING)
    if isinstance(text, list):
        return sum(len(enc.encode(t)) for t in text if t)
    return len(enc.encode(text))


def has_usage_metadata(response) -> bool:
    """True if any ChatGeneration in `response` carries a `usage_metadata`
    dict on its `.message`. False for text-completion responses (no
    `.message`) and for chat responses where every generation's
    usage_metadata is None.
    """
    return any(
        getattr(getattr(g, "message", None), "usage_metadata", None) for gens in response.generations for g in gens
    )


def response_text(response) -> str:
    """Join all generation text content from `response` for tiktoken counting.
    Generations with empty/None text are skipped.
    """
    return " ".join(g.text for gens in response.generations for g in gens if g.text)
