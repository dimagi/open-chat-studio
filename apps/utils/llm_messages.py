"""Helpers for keeping message content acceptable to LLM providers.

Anthropic rejects a text content block that holds only whitespace with
``400 messages: text content blocks must contain non-whitespace text``. Participants can
legitimately send nothing (an empty Connect message, a transcript that came back blank),
so we substitute a visible placeholder instead of failing the turn.

The placeholder is applied both when building the outgoing turn and when the stored
message is replayed as history -- a message persisted with empty content would otherwise
poison every subsequent turn in that session.
"""

EMPTY_MESSAGE_PLACEHOLDER = "[empty message]"


def ensure_non_empty_text(content: str | None) -> str:
    """Return ``content`` unchanged, or the placeholder if it has no non-whitespace text.

    ``None`` is accepted because JSON-sourced content (e.g. ``EvaluationMessage.input``)
    can legitimately be null.
    """
    if content and content.strip():
        return content
    return EMPTY_MESSAGE_PLACEHOLDER
