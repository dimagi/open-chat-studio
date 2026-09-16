from unittest.mock import MagicMock, patch

import pytest

from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS, Model
from apps.service_providers.management.commands.check_llm_model_usage import Command

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_db_model(provider_type, name):
    """Minimal stand-in for an LlmProviderModel DB row."""
    m = MagicMock()
    m.type = provider_type
    m.name = name
    m.deprecated = True
    return m


# A minimal fake DEFAULT_LLM_PROVIDER_MODELS used by unit tests so they are
# independent of changes to the real model catalogue.
FAKE_MODELS = {
    # Provider where the deprecated model has an explicit replacement that
    # differs from the provider default – the key scenario being tested.
    "fake_provider": [
        Model("provider-default", 100_000, is_default=True),
        Model("old-explicit", 100_000, deprecated=True, replacement="specific-replacement"),
        Model("old-no-replacement", 100_000, deprecated=True),
    ],
    # Provider with no is_default model at all.
    "no_default_provider": [
        Model("orphan-deprecated", 100_000, deprecated=True, replacement="orphan-replacement"),
    ],
}

_PATCH = "apps.service_providers.management.commands.check_llm_model_usage.DEFAULT_LLM_PROVIDER_MODELS"


# ---------------------------------------------------------------------------
# Unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestGetSuggestedReplacement:
    def setup_method(self):
        self.cmd = Command()

    @patch(_PATCH, FAKE_MODELS)
    def test_explicit_replacement_preferred_over_provider_default(self):
        """Explicit replacement wins even when the provider has an is_default model."""
        model = _mock_db_model("fake_provider", "old-explicit")
        result = self.cmd._get_suggested_replacement(model)
        # Must return "specific-replacement", NOT "provider-default"
        assert result == "specific-replacement"

    @patch(_PATCH, FAKE_MODELS)
    def test_falls_back_to_provider_default_when_no_replacement(self):
        """No explicit replacement → fall back to the provider's is_default model."""
        model = _mock_db_model("fake_provider", "old-no-replacement")
        result = self.cmd._get_suggested_replacement(model)
        assert result == "provider-default"

    @patch(_PATCH, FAKE_MODELS)
    def test_returns_none_for_unknown_provider(self):
        """A provider not in DEFAULT_LLM_PROVIDER_MODELS returns None."""
        model = _mock_db_model("nonexistent_provider", "some-model")
        assert self.cmd._get_suggested_replacement(model) is None

    @patch(_PATCH, FAKE_MODELS)
    def test_explicit_replacement_used_when_no_provider_default(self):
        """Explicit replacement is used even when the provider has no is_default model."""
        model = _mock_db_model("no_default_provider", "orphan-deprecated")
        assert self.cmd._get_suggested_replacement(model) == "orphan-replacement"


# ---------------------------------------------------------------------------
# Regression tests against the real DEFAULT_LLM_PROVIDER_MODELS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_type", "model_name"),
    [
        pytest.param("google_vertex_ai", "gemini-2.5-pro", id="vertex-gemini-2.5-pro"),
        pytest.param("google_vertex_ai", "gemini-2.5-flash", id="vertex-gemini-2.5-flash"),
        pytest.param("google_vertex_ai", "gemini-2.5-flash-lite", id="vertex-gemini-2.5-flash-lite"),
    ],
)
def test_vertex_deprecated_model_uses_explicit_replacement(provider_type, model_name):
    """
    Regression guard for the google_vertex_ai / gemini-2.5-* cases described
    in issue #4509.

    The real config has e.g.:
        Model("gemini-2.5-pro", ..., deprecated=True, replacement="gemini-3.6-flash")
        Model("gemini-3.5-flash", ..., is_default=True)

    The old code returned "gemini-3.5-flash" (the provider default) instead of
    "gemini-3.6-flash" (the configured replacement), sending teams to the wrong
    migration target when they ran ``check_llm_model_usage --deprecated-only``
    before the Gemini 2.5 Vertex AI deprecation deadline (2026-10-20).
    """
    cmd = Command()
    db_model = _mock_db_model(provider_type, model_name)
    result = cmd._get_suggested_replacement(db_model)

    provider_models = DEFAULT_LLM_PROVIDER_MODELS.get(provider_type, [])
    configured = next((m for m in provider_models if m.name == model_name), None)
    assert configured is not None, f"{model_name} must still be listed under {provider_type}"
    assert configured.replacement, f"{model_name} must still have an explicit replacement set"

    assert result == configured.replacement, (
        f"Expected '{configured.replacement}' but got '{result}'. "
        "check_llm_model_usage must honour the explicit replacement, "
        "not the provider default, to stay consistent with notify_deprecated_models."
    )
