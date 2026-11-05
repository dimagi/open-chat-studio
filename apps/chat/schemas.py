from django.db import models
from django.utils.translation import gettext_lazy as _
from pydantic import BaseModel

from apps.service_providers.moderation_service.schemas import ModerationOutput


class ModerationSkipReason(models.TextChoices):
    RATE_LIMITED = "rate_limited", _("Rate limit")
    ERROR = "error", _("Error")
    TIMEOUT = "timeout", _("Timeout")


class ModerationDetail(BaseModel):
    """Provider output, skip reason and rule hits recorded on a moderated human message."""

    output: ModerationOutput | None = None
    skip_reason: ModerationSkipReason | None = None
    rules: list[str] = []
