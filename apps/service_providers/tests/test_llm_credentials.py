import json

import pytest

from apps.service_providers.llm_service.credentials import (
    get_provider_credentials_for_type,
    get_provider_credentials_from_env,
)
from apps.service_providers.models import LlmProviderTypes

_PROVIDER_VARS = [
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_ORGANIZATION",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY",
    "GOOGLE_VERTEX_AI_CREDENTIALS_JSON",
    "GOOGLE_VERTEX_AI_LOCATION",
    "GOOGLE_VERTEX_AI_API_TRANSPORT",
    "DEEPSEEK_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "GROQ_API_KEY",
    "PERPLEXITY_API_KEY",
]


@pytest.fixture()
def clean_env(monkeypatch):
    """Strip all provider env vars so tests start from a known state."""
    for var in _PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_returns_empty_when_no_env_vars_set(clean_env):
    assert get_provider_credentials_from_env() == []


def test_openai_minimal_config(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    creds = get_provider_credentials_for_type(LlmProviderTypes.openai)
    assert creds is not None
    assert creds.config == {"openai_api_key": "sk-test"}


def test_openai_optional_fields_only_included_when_set(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("OPENAI_API_BASE", "https://proxy.example.com")
    clean_env.setenv("OPENAI_ORGANIZATION", "org-1")
    creds = get_provider_credentials_for_type(LlmProviderTypes.openai)
    assert creds.config == {
        "openai_api_key": "sk-test",
        "openai_api_base": "https://proxy.example.com",
        "openai_organization": "org-1",
    }


def test_anthropic_uses_default_api_base(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant")
    creds = get_provider_credentials_for_type(LlmProviderTypes.anthropic)
    assert creds.config == {
        "anthropic_api_key": "sk-ant",
        "anthropic_api_base": "https://api.anthropic.com",
    }


def test_azure_requires_both_api_key_and_endpoint(clean_env):
    clean_env.setenv("AZURE_OPENAI_API_KEY", "key")
    assert get_provider_credentials_for_type(LlmProviderTypes.azure) is None

    clean_env.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    creds = get_provider_credentials_for_type(LlmProviderTypes.azure)
    assert creds is not None
    assert creds.config["openai_api_base"] == "https://example.openai.azure.com/"
    assert creds.config["openai_api_version"] == "2024-02-15-preview"


def test_google_vertex_ai_parses_credentials_json(clean_env):
    payload = {"type": "service_account", "project_id": "test"}
    clean_env.setenv("GOOGLE_VERTEX_AI_CREDENTIALS_JSON", json.dumps(payload))
    creds = get_provider_credentials_for_type(LlmProviderTypes.google_vertex_ai)
    assert creds.config["credentials_json"] == payload
    assert creds.config["location"] == "global"
    assert creds.config["api_transport"] == "rest"


def test_google_vertex_ai_invalid_json_raises(clean_env):
    clean_env.setenv("GOOGLE_VERTEX_AI_CREDENTIALS_JSON", "{not valid json")
    with pytest.raises(ValueError, match="not valid JSON"):
        get_provider_credentials_for_type(LlmProviderTypes.google_vertex_ai)


def test_returns_one_entry_per_configured_provider(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "k1")
    clean_env.setenv("ANTHROPIC_API_KEY", "k2")
    clean_env.setenv("GROQ_API_KEY", "k3")
    slugs = sorted(creds.type.slug for creds in get_provider_credentials_from_env())
    assert slugs == ["anthropic", "groq", "openai"]
