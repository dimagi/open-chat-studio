"""Provider SDK errors as the SDKs and the LLM call wrapper raise them."""

import httpx

from apps.chat.exceptions import ProviderConfigurationError
from apps.service_providers.llm_service.error_classification import translate_provider_errors


def status_error(cls, status: int, message: str, body: dict | None = None):
    """An SDK error for an HTTP response, e.g. ``status_error(anthropic.RateLimitError, 429, "...")``."""
    request = httpx.Request("POST", "https://api.example.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=body or {})


def converted(error: Exception) -> ProviderConfigurationError:
    """The ProviderConfigurationError the LLM call wrapper raises in place of a team-actionable SDK error."""
    try:
        with translate_provider_errors():
            raise error
    except ProviderConfigurationError as converted_error:
        return converted_error
    raise AssertionError(f"{error!r} was not converted")
