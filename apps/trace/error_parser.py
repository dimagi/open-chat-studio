"""
Errors can be categorized or tagged based on where or why it occurs.
"""

from django.db import models
from openai import BadRequestError, OpenAIError

from apps.chat.exceptions import AudioSynthesizeException
from apps.pipelines.exceptions import CodeNodeRunError


class ErrorCategory(models.TextChoices):
    # LLM Provider tags
    OPENAI = "OpenAI"
    # Pipeline tags
    CODE_NODE = "code node"
    PIPELINE_RUN = "Pipeline run"
    # API call tags
    BAD_API_CALL = "bad api call"
    # Processing tags
    AUDIO_SYNTHESIS = "audio synthesis"
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
