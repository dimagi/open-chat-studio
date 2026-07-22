"""Custom Django model fields and field-value helpers."""

import re

from django.db import models

# NUL byte plus the control characters PostgreSQL cannot store in text/JSONB columns,
# excluding the common whitespace chars \t (0x09), \n (0x0a) and \r (0x0d).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_control_chars(value: str) -> str:
    """Remove NUL bytes and control characters that PostgreSQL text/JSONB columns cannot store.

    LLM-generated content can contain these characters; PostgreSQL rejects them, so they must be
    stripped before saving. Common whitespace (\\t, \\n, \\r) is preserved.
    """
    return _CONTROL_CHAR_RE.sub("", value)


def sanitize_json_data(data):
    """Recursively remove NUL bytes and control characters from every string in a JSON structure.

    PostgreSQL's JSONB type cannot store these characters in text values, so they are stripped
    throughout dicts and lists. Returns a sanitized copy; non-string primitives pass through.
    """
    if isinstance(data, dict):
        return {key: sanitize_json_data(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [sanitize_json_data(item) for item in data]
    elif isinstance(data, str):
        return sanitize_control_chars(data)
    else:
        # Return primitives (int, float, bool, None) as-is
        return data


def as_int(value) -> int | None:
    """Convert a value to an int, returning None if it can't be (e.g. a malformed id from JSON).

    Only ints and integer strings convert. Everything else maps to None, including booleans
    (``int(True)`` would coerce to 1) and floats (``int(1.9)`` would truncate to 1 and point a
    FK at the wrong row).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class SanitizedJSONField(models.JSONField):
    """
    A JSONField that automatically sanitizes data to remove null bytes and control characters
    that are incompatible with PostgreSQL's JSONB type.

    This field should be used for any JSON data that may contain LLM-generated content,
    as LLMs can sometimes produce control characters that PostgreSQL cannot store in JSONB.
    """

    def get_prep_value(self, value):
        """Sanitize the value before saving to the database."""
        if value is not None:
            value = sanitize_json_data(value)
        return super().get_prep_value(value)
