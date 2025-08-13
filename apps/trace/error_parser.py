"""
Errors can be categorized or tagged based on where or why it occurs.
"""

import re

from django.db import models
from openai import OpenAIError

from apps.chat.exceptions import AudioSynthesizeException
from apps.pipelines.exceptions import CodeNodeRunError

FLOAT_PATTERN = r"[+-]?([0-9]*[.])?[0-9]+"  # matches floating point numbers


class ErrorCategory(models.TextChoices):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate limit"
    AUTH = "auth"
    CODE_NODE = "code node"
    EMPTY_USER_MESSAGE = "empty user message"
    UNSUPPORTED_TEMPERATURE = "unsupported temperature"
    SYNTHESIZE_AUDIO = "synthesize audio"
    UNKNOWN = "unknown"


def get_tags_from_error(error: Exception):
    tags = []
    if isinstance(error, OpenAIError):
        tags.append(_parse_openai_error(error))
    elif isinstance(error, CodeNodeRunError):
        tags.append(ErrorCategory.CODE_NODE)
    elif isinstance(error, AudioSynthesizeException):
        tags.append(ErrorCategory.SYNTHESIZE_AUDIO)
    else:
        tags.append(ErrorCategory.UNKNOWN)
    return tags


def _parse_openai_error(error: OpenAIError) -> ErrorCategory:
    if "Incorrect API key provided" in error.message:
        return ErrorCategory.AUTH
    elif "Message content must be non-empty" in error.message:
        return ErrorCategory.EMPTY_USER_MESSAGE
    elif re.match(rf".*'temperature' does not support {FLOAT_PATTERN} with this model", error.message):
        return ErrorCategory.UNSUPPORTED_TEMPERATURE
    return ErrorCategory.UNKNOWN
