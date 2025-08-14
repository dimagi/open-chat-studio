"""
Errors can be categorized or tagged based on where or why it occurs.
"""

from django.db import models
from openai import BadRequestError, OpenAIError

from apps.chat.exceptions import AudioSynthesizeException
from apps.pipelines.exceptions import CodeNodeRunError


class ErrorCategory(models.TextChoices):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate limit"
    AUTH = "auth"
    CODE_NODE = "code node"
    BAD_API_CALL = "bad api call"
    AUDIO_SYNTHESIS = "audio synthesis"
    UNKNOWN = "unknown"


def get_tags_from_error(error: Exception):
    tags = []
    if isinstance(error, OpenAIError):
        tags.append(_parse_openai_error(error))
    elif isinstance(error, CodeNodeRunError):
        tags.append(ErrorCategory.CODE_NODE)
    elif isinstance(error, AudioSynthesizeException):
        tags.append(ErrorCategory.AUDIO_SYNTHESIS)
    else:
        tags.append(ErrorCategory.UNKNOWN)
    return tags


def _parse_openai_error(error: OpenAIError) -> ErrorCategory:
    if "Incorrect API key provided" in error.message:
        return ErrorCategory.AUTH
    elif isinstance(error, BadRequestError):
        return ErrorCategory.BAD_API_CALL
    return ErrorCategory.UNKNOWN
