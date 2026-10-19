from abc import ABC, abstractmethod

import openai
import pydantic

from apps.service_providers.openai_client import build_openai_client

from .schemas import ModerationCategory, ModerationOutput

OPENAI_MODERATION_MODEL = "omni-moderation-latest"


class ModerationRateLimited(Exception):
    """The provider refused the request because the account's rate limit was reached."""


class ModerationService(pydantic.BaseModel, ABC):
    @abstractmethod
    def moderate(self, text: str) -> ModerationOutput: ...


class OpenAIModerationService(ModerationService):
    openai_api_key: pydantic.SecretStr
    openai_api_base: str | None = None
    openai_organization: str | None = None
    timeout: float | None = None
    max_retries: int = openai.DEFAULT_MAX_RETRIES

    def moderate(self, text: str) -> ModerationOutput:
        try:
            response = self._client().moderations.create(model=OPENAI_MODERATION_MODEL, input=text)
        except openai.RateLimitError as e:
            raise ModerationRateLimited(str(e)) from e
        result = response.results[0]
        flags = result.categories.model_dump(by_alias=True)
        scores = result.category_scores.model_dump(by_alias=True)
        return ModerationOutput(
            {
                name: ModerationCategory(flagged=flagged, score=scores[name])
                for name, flagged in flags.items()
                if flagged is not None and scores.get(name) is not None
            }
        )

    def _client(self) -> openai.OpenAI:
        return build_openai_client(
            api_key=self.openai_api_key,
            organization=self.openai_organization,
            base_url=self.openai_api_base,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
