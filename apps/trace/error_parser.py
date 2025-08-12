"""
Errors can be categorized or tagged based on where or why it occurs.
"""

from openai import OpenAIError


def get_tags_from_error(error: Exception):
    tags = []
    if isinstance(error, OpenAIError):
        tags.append("openai")
        if "Incorrect API key provided" in error.message:
            tags.append("setup")
    return tags
