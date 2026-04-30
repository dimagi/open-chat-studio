"""Load LLM provider credentials from environment variables.

Used by the `bootstrap_data` management command and the integration test
fixtures so the same env var → config mapping is shared between dev seeding
and live integration tests.

Each loader returns config dicts whose keys match the corresponding
`apps.service_providers.forms.*ConfigForm`, so the result can be assigned
straight to `LlmProvider.config`.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from apps.service_providers.models import LlmProviderTypes


@dataclass(frozen=True)
class ProviderCredentials:
    type: LlmProviderTypes
    name: str
    config: dict


def _openai() -> ProviderCredentials | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    config = {"openai_api_key": api_key}
    if api_base := os.environ.get("OPENAI_API_BASE"):
        config["openai_api_base"] = api_base
    if organization := os.environ.get("OPENAI_ORGANIZATION"):
        config["openai_organization"] = organization
    return ProviderCredentials(LlmProviderTypes.openai, "OpenAI", config)


def _anthropic() -> ProviderCredentials | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return ProviderCredentials(
        LlmProviderTypes.anthropic,
        "Anthropic",
        {
            "anthropic_api_key": api_key,
            "anthropic_api_base": os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com"),
        },
    )


def _google() -> ProviderCredentials | None:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    return ProviderCredentials(LlmProviderTypes.google, "Google Gemini", {"google_api_key": api_key})


def _google_vertex_ai() -> ProviderCredentials | None:
    credentials_json = os.environ.get("GOOGLE_VERTEX_AI_CREDENTIALS_JSON")
    if not credentials_json:
        return None
    try:
        credentials = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_VERTEX_AI_CREDENTIALS_JSON is not valid JSON: {exc}") from exc
    return ProviderCredentials(
        LlmProviderTypes.google_vertex_ai,
        "Google Vertex AI",
        {
            "credentials_json": credentials,
            "location": os.environ.get("GOOGLE_VERTEX_AI_LOCATION", "global"),
            "api_transport": os.environ.get("GOOGLE_VERTEX_AI_API_TRANSPORT", "rest"),
        },
    )


def _deepseek() -> ProviderCredentials | None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    return ProviderCredentials(LlmProviderTypes.deepseek, "DeepSeek", {"deepseek_api_key": api_key})


def _azure() -> ProviderCredentials | None:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not (api_key and endpoint):
        return None
    return ProviderCredentials(
        LlmProviderTypes.azure,
        "Azure OpenAI",
        {
            "openai_api_key": api_key,
            "openai_api_base": endpoint,
            "openai_api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        },
    )


def _groq() -> ProviderCredentials | None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    return ProviderCredentials(LlmProviderTypes.groq, "Groq", {"openai_api_key": api_key})


def _perplexity() -> ProviderCredentials | None:
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return None
    return ProviderCredentials(LlmProviderTypes.perplexity, "Perplexity", {"openai_api_key": api_key})


_LOADERS: list[Callable[[], ProviderCredentials | None]] = [
    _openai,
    _anthropic,
    _google,
    _google_vertex_ai,
    _deepseek,
    _azure,
    _groq,
    _perplexity,
]


def get_provider_credentials_from_env() -> list[ProviderCredentials]:
    """Return one entry per LLM provider that has its env vars configured."""
    return [creds for loader in _LOADERS if (creds := loader())]


def get_provider_credentials_for_type(provider_type: LlmProviderTypes) -> ProviderCredentials | None:
    return next((c for c in get_provider_credentials_from_env() if c.type == provider_type), None)
