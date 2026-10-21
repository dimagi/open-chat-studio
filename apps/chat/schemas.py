from typing import Literal

from pydantic import BaseModel

from apps.service_providers.moderation_service.schemas import ModerationOutput


class ModerationDetail(BaseModel):
    """Provider output, skip reason and rule hits recorded on a moderated human message."""

    output: ModerationOutput | None = None
    skip_reason: Literal["rate_limited", "error", "timeout"] | None = None
    rules: list[str] = []
