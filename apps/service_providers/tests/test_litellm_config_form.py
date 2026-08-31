from unittest.mock import Mock

import pytest

from apps.service_providers.forms import LiteLLMConfigForm


class TestLiteLLMConfigForm:
    """LiteLLM has no canonical endpoint like groq/perplexity/minimax, so unlike those
    forms it needs a required, user-entered base URL - normalized so a URL entered with
    or without a trailing /v1 doesn't produce /v1/v1.

    Field names are api_key/api_base, not openai_api_key/openai_api_base like the other
    OpenAI-compatible forms - see LiteLLMConfigForm.save() and __init__ in forms.py for
    the translation to and from the openai_api_key/openai_api_base storage format
    OpenAIGenericService itself expects.
    """

    def test_api_key_is_required(self):
        form = LiteLLMConfigForm(None, data={"api_base": "https://proxy.example.com"})
        assert not form.is_valid()
        assert "api_key" in form.errors

    def test_base_url_is_required(self):
        form = LiteLLMConfigForm(None, data={"api_key": "test"})
        assert not form.is_valid()
        assert "api_base" in form.errors

    def test_malformed_base_url_is_rejected(self):
        form = LiteLLMConfigForm(None, data={"api_key": "test", "api_base": "not-a-url"})
        assert not form.is_valid()
        assert "api_base" in form.errors

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
        form = LiteLLMConfigForm(None, data={"api_key": "test", "api_base": raw})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["api_base"] == normalized

    def test_save_stores_the_openai_generic_service_field_names(self):
        """The saved config is what OpenAIGenericService(**config) actually expects."""
        form = LiteLLMConfigForm(None, data={"api_key": "sk-test", "api_base": "https://proxy.example.com"})
        assert form.is_valid(), form.errors

        instance = form.save(Mock(config=None))

        assert instance.config == {
            "openai_api_key": "sk-test",
            "openai_api_base": "https://proxy.example.com/v1",
        }

    def test_api_key_is_obfuscated_for_display(self):
        """initial arrives in storage format (openai_api_key/openai_api_base, from
        LlmProvider.config via get_form_initial) - not this form's own api_key/api_base
        field names. Regression test: without __init__ translating between the two, this
        field renders blank on the edit page instead of obfuscated."""
        form = LiteLLMConfigForm(
            None, initial={"openai_api_key": "sk-test-value", "openai_api_base": "https://proxy.example.com/v1"}
        )
        assert form["api_key"].value() == "sk-t...ue"
        assert form["api_base"].value() == "https://proxy.example.com/v1"
