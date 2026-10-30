from pydantic import BaseModel, model_validator

from apps.chat.schemas import ModerationSkipReason
from apps.service_providers.moderation_service.schemas import ModerationOutput


class ModerationTaskResult(BaseModel):
    """The result of `moderate_message`: the provider output, or the reason there is none."""

    output: ModerationOutput | None = None
    skip_reason: ModerationSkipReason | None = None

    @model_validator(mode="after")
    def _has_exactly_one_outcome(self):
        if (self.output is None) == (self.skip_reason is None):
            raise ValueError("Set exactly one of output and skip_reason")
        return self
