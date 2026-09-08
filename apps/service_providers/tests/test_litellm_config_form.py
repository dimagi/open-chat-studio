import pytest

from apps.service_providers.forms import LiteLLMConfigForm


class TestLiteLLMConfigForm:
    """LiteLLM has no canonical endpoint like groq/perplexity/minimax, so unlike those
    forms it needs a required, user-entered base URL - normalized so a URL entered with
    or without a trailing /v1 doesn't produce /v1/v1."""

    def test_api_key_is_required(self):
        form = LiteLLMConfigForm(None, data={"openai_api_base": "https://proxy.example.com"})
        assert not form.is_valid()
        assert "openai_api_key" in form.errors

    def test_base_url_is_required(self):
        form = LiteLLMConfigForm(None, data={"openai_api_key": "test"})
        assert not form.is_valid()
        assert "openai_api_base" in form.errors

    def test_malformed_base_url_is_rejected(self):
        form = LiteLLMConfigForm(None, data={"openai_api_key": "test", "openai_api_base": "not-a-url"})
        assert not form.is_valid()
        assert "openai_api_base" in form.errors

    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            pytest.param("https://proxy.example.com", "https://proxy.example.com/v1", id="no-slash-no-v1"),
            pytest.param("https://proxy.example.com/", "https://proxy.example.com/v1", id="trailing-slash-no-v1"),
            pytest.param("https://proxy.example.com/v1", "https://proxy.example.com/v1", id="already-v1"),
            pytest.param("https://proxy.example.com/v1/", "https://proxy.example.com/v1", id="already-v1-slash"),
        ],
    )
    def test_normalizes_base_url(self, raw, normalized):
        form = LiteLLMConfigForm(None, data={"openai_api_key": "test", "openai_api_base": raw})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["openai_api_base"] == normalized

    def test_api_key_is_obfuscated_for_display(self):
        form = LiteLLMConfigForm(
            None, initial={"openai_api_key": "sk-test-value", "openai_api_base": "https://proxy.example.com/v1"}
        )
        assert form["openai_api_key"].value() == "sk-t...ue"
