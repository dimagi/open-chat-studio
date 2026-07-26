"""Verify that `LlmService.get_chat_model` stamps `metadata["ocs_provider_type"]`
onto every chat model it returns. Cost tracking depends on this metadata being
present on every LangChain callback to classify usage into pricing rules.
"""

import pytest

from apps.service_providers.llm_service import (
    AnthropicLlmService,
    AzureLlmService,
    OpenAILlmService,
)
from apps.service_providers.llm_service.main import DeepSeekLlmService, GoogleLlmService, OpenAIGenericService


def _service_with_type(service_cls, ocs_type: str, **kwargs):
    """Build an LlmService subclass with `_type` set to the OCS provider slug.

    `_type` is a private pydantic field assigned by the factory in real code;
    here we set it manually for the test.
    """
    service = service_cls(**kwargs)
    service._type = ocs_type
    return service


# ---------- _tag_chat_model directly ------------------------------------------


class TestTagChatModel:
    """`_tag_chat_model`: direct unit test of the metadata mutation."""

    def test_stamps_slug_onto_model(self):
        service = _service_with_type(OpenAILlmService, "openai", openai_api_key="test")
        model = service.get_chat_model("gpt-4o-mini")
        assert model.metadata["ocs_provider_type"] == "openai"

    def test_preserves_existing_metadata(self):
        service = _service_with_type(OpenAILlmService, "openai", openai_api_key="test")
        model = service.get_chat_model("gpt-4o-mini")
        # Mutate AFTER tagging — the existing entry must survive a re-tag.
        model.metadata["custom_key"] = "custom_value"
        service._tag_chat_model(model)
        assert model.metadata["custom_key"] == "custom_value"
        assert model.metadata["ocs_provider_type"] == "openai"


# ---------- per-provider get_chat_model ---------------------------------------


@pytest.mark.parametrize(
    ("service", "slug", "model_name"),
    [
        pytest.param(
            _service_with_type(OpenAILlmService, "openai", openai_api_key="test"),
            "openai",
            "gpt-4o-mini",
            id="openai",
        ),
        pytest.param(
            _service_with_type(
                AzureLlmService,
                "azure",
                openai_api_key="test",
                openai_api_base="https://example.openai.azure.com/",
                openai_api_version="2024-02-15-preview",
            ),
            "azure",
            "gpt-4o",
            id="azure",
        ),
        pytest.param(
            _service_with_type(
                AnthropicLlmService,
                "anthropic",
                anthropic_api_key="test",
                anthropic_api_base="https://api.anthropic.com",
            ),
            "anthropic",
            "claude-3-5-sonnet-20241022",
            id="anthropic",
        ),
        pytest.param(
            _service_with_type(
                DeepSeekLlmService,
                "deepseek",
                deepseek_api_key="test",
                deepseek_api_base="https://api.deepseek.com",
            ),
            "deepseek",
            "deepseek-chat",
            id="deepseek",
        ),
        pytest.param(
            _service_with_type(GoogleLlmService, "google", google_api_key="test"),
            "google",
            "gemini-2.0-flash",
            id="google",
        ),
        pytest.param(
            _service_with_type(
                OpenAIGenericService,
                "groq",
                openai_api_key="test",
                openai_api_base="https://api.groq.com/openai/v1/",
            ),
            "groq",
            "llama-3.1-8b-instant",
            id="groq-via-openai-generic",
        ),
        pytest.param(
            _service_with_type(
                OpenAIGenericService,
                "perplexity",
                openai_api_key="test",
                openai_api_base="https://api.perplexity.ai/",
            ),
            "perplexity",
            "llama-3.1-sonar-small-128k-online",
            id="perplexity-via-openai-generic",
        ),
    ],
)
def test_get_chat_model_stamps_provider_type(service, slug, model_name):
    """Every LlmService subclass tags `ocs_provider_type` on the chat model it returns.

    Groq and Perplexity collapse to the same LangChain `_type` ("openai-chat")
    because they route through ChatOpenAI — the metadata stamp is the only
    place we can distinguish them.
    """
    model = service.get_chat_model(model_name)
    assert model.metadata is not None
    assert model.metadata.get("ocs_provider_type") == slug


def test_chat_model_keeps_base_chat_model_interface():
    """Tagging via `.metadata = ...` (vs `.with_config(...)`) preserves the
    BaseChatModel return type so callers can still chain `with_structured_output`,
    `bind_tools`, etc.
    """
    service = _service_with_type(OpenAILlmService, "openai", openai_api_key="test")
    model = service.get_chat_model("gpt-4o-mini")
    assert hasattr(model, "with_structured_output")
    assert hasattr(model, "bind_tools")
