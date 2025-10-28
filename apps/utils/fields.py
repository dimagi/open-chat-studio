"""Custom Django model fields."""

import re

from django.db import models


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
            value = self._sanitize_json_data(value)
        return super().get_prep_value(value)

    def _sanitize_json_data(self, data):
        """
        Recursively sanitize JSON data by removing null bytes and control characters.

        PostgreSQL's JSONB type cannot store null bytes (\u0000) and some control characters
        in text values. This function removes these characters from strings throughout the
        JSON structure.

        Args:
            data: The data to sanitize (dict, list, str, or primitive)

        Returns:
            Sanitized copy of the data
        """
        if isinstance(data, dict):
            return {key: self._sanitize_json_data(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_json_data(item) for item in data]
        elif isinstance(data, str):
            # Remove null bytes and control characters (except common whitespace like \n, \r, \t)
            # This removes characters in the range \x00-\x1f except \t (0x09), \n (0x0a), \r (0x0d)
            sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', data)
            return sanitized
        else:
            # Return primitives (int, float, bool, None) as-is
            return data
