"""Provider SDK errors as the SDKs and the LLM call wrapper raise them."""

import httpx

from apps.chat.exceptions import ProviderConfigurationError
from apps.service_providers.llm_service.error_classification import translate_provider_errors

_REQUEST = httpx.Request("POST", "https://api.example.com/v1/messages")


def status_error(cls, status: int, message: str, body: dict | None = None):
    """An SDK error for an HTTP response, e.g. ``status_error(anthropic.RateLimitError, 429, "...")``."""
    return cls(message, response=httpx.Response(status, request=_REQUEST), body=body or {})


def connection_error(cls):
    """An SDK error for a request that got no response, e.g. ``connection_error(openai.APIConnectionError)``."""
    return cls(request=_REQUEST)


def converted(error: Exception) -> ProviderConfigurationError:
    """The ProviderConfigurationError the LLM call wrapper raises in place of a team-actionable SDK error."""
    try:
        with translate_provider_errors():
            raise error
    except ProviderConfigurationError as converted_error:
        return converted_error
    raise AssertionError(f"{error!r} was not converted")
