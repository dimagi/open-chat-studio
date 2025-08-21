"""
Errors can be categorized or tagged based on where or why it occurs.
"""

from django.db import models
from openai import BadRequestError, OpenAIError

from apps.chat.exceptions import AudioSynthesizeException
from apps.pipelines.exceptions import CodeNodeRunError


class ErrorCategory(StrEnum):
    # LLM Provider tags
    OPENAI = "openai"
    # Pipeline tags
    CODE_NODE = "python node"
    PIPELINE_RUN = "internal"
    # API call tags
    BAD_API_CALL = "api"
    # Processing tags
    AUDIO_SYNTHESIS = "text to speech"
    UNKNOWN = "unknown"


def get_tags_from_error(error: Exception):
    tags = []
    if isinstance(error, OpenAIError):
        tags.extend(_parse_openai_error(error))
    elif isinstance(error, CodeNodeRunError):
        tags.append(ErrorCategory.CODE_NODE)
        tags.append(ErrorCategory.PIPELINE_RUN)
    elif isinstance(error, AudioSynthesizeException):
        tags.append(ErrorCategory.AUDIO_SYNTHESIS)
    else:
        tags.append(ErrorCategory.UNKNOWN)
    return tags


def _parse_openai_error(error: OpenAIError) -> ErrorCategory:
    tags = [ErrorCategory.OPENAI]
    if isinstance(error, BadRequestError):
        tags.append(ErrorCategory.BAD_API_CALL)
    else:
        tags.append(ErrorCategory.UNKNOWN)
    return tags
