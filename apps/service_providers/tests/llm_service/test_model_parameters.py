import pytest
from pydantic import ValidationError

from apps.service_providers.llm_service.default_models import LLM_MODEL_PARAMETERS
from apps.service_providers.llm_service.model_parameters import (
    ClaudeOpus47Parameters,
    ClaudeOpus55Parameters,
    ClaudeSonnet46Parameters,
    GPT6SolParameters,
    GPT52Parameters,
    get_schema,
)


class TestGPT52ParametersNoneEffort:
    """When effort is switched to 'none', temperature and top_p start as null
    (cleared by the frontend VisibleWhenWrapper). The backend must not reject
    this — it should apply sensible defaults so the save succeeds."""

    def test_temperature_defaults_when_effort_is_none_and_temperature_is_null(self):
        params = GPT52Parameters(effort="none", temperature=None, top_p=1.0)
        assert params.temperature == 0.7

    def test_top_p_defaults_when_effort_is_none_and_top_p_is_null(self):
        params = GPT52Parameters(effort="none", temperature=0.7, top_p=None)
        assert params.top_p == 1.0

    def test_both_default_when_effort_is_none_and_both_are_null(self):
        params = GPT52Parameters(effort="none", temperature=None, top_p=None)
        assert params.temperature == 0.7
        assert params.top_p == 1.0

    def test_explicit_values_are_preserved_when_effort_is_none(self):
        params = GPT52Parameters(effort="none", temperature=0.5, top_p=0.9)
        assert params.temperature == 0.5
        assert params.top_p == 0.9

    def test_temperature_and_top_p_must_be_null_when_effort_is_not_none(self):
        with pytest.raises(ValidationError):
            GPT52Parameters(effort="medium", temperature=0.7, top_p=None)

    def test_temperature_and_top_p_are_null_when_effort_is_medium(self):
        params = GPT52Parameters(effort="medium")
        assert params.temperature is None
        assert params.top_p is None


class TestGPT52ParametersSchema:
    """The JSON schema for GPT52Parameters must advertise show defaults so the
    frontend can populate the sliders immediately when effort switches to 'none'."""

    def test_temperature_has_default_on_show_in_schema(self):
        schema = get_schema(GPT52Parameters)
        assert schema["properties"]["temperature"]["ui:onShowDefault"] == 0.7

    def test_top_p_has_default_on_show_in_schema(self):
        schema = get_schema(GPT52Parameters)
        assert schema["properties"]["top_p"]["ui:onShowDefault"] == 1.0


class TestClaudeSonnet46Parameters:
    """Claude Sonnet 4.6 was released after Opus 4.6 and does not support
    temperature. Verify the parameter class omits it entirely."""

    def test_temperature_not_in_fields(self):
        assert "temperature" not in ClaudeSonnet46Parameters.model_fields

    def test_temperature_not_in_model_dump(self):
        params = ClaudeSonnet46Parameters()
        assert "temperature" not in params.model_dump()

    def test_defaults(self):
        params = ClaudeSonnet46Parameters()
        assert params.max_tokens == 32000
        assert params.effort == "high"
        assert params.adaptive_thinking is False

    def test_effort_and_adaptive_thinking_configurable(self):
        params = ClaudeSonnet46Parameters(effort="low", adaptive_thinking=True)
        assert params.effort == "low"
        assert params.adaptive_thinking is True


class TestClaudeOpus55Parameters:
    """Opus 5.5 defaults to 'medium' effort where the other models on
    ClaudeOpus47Parameters default to 'high'."""

    def test_effort_defaults_to_medium(self):
        assert ClaudeOpus55Parameters().effort == "medium"

    def test_opus_47_default_is_unchanged(self):
        assert ClaudeOpus47Parameters().effort == "high"

    def test_effort_range_matches_opus_47(self):
        opus_55 = get_schema(ClaudeOpus55Parameters)["properties"]["effort"]["enum"]
        opus_47 = get_schema(ClaudeOpus47Parameters)["properties"]["effort"]["enum"]
        assert opus_55 == opus_47

    def test_other_fields_match_opus_47(self):
        params = ClaudeOpus55Parameters()
        assert params.max_tokens == 32000
        assert params.adaptive_thinking is False
        assert "temperature" not in params.model_dump()

    def test_registered_for_claude_opus_5_5(self):
        assert LLM_MODEL_PARAMETERS["claude-opus-5-5"] is ClaudeOpus55Parameters


class TestGPT6SolParameters:
    """gpt-6-sol and gpt-6-luna expose the six-level effort range and no sampling
    parameters, including at 'none' effort."""

    def test_effort_defaults_to_medium(self):
        assert GPT6SolParameters().effort == "medium"

    def test_effort_range(self):
        assert get_schema(GPT6SolParameters)["properties"]["effort"]["enum"] == [
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]

    def test_no_sampling_fields(self):
        assert "temperature" not in GPT6SolParameters.model_fields
        assert "top_p" not in GPT6SolParameters.model_fields

    @pytest.mark.parametrize("effort", ["none", "max"])
    def test_sampling_overrides_are_dropped(self, effort):
        """An override carried over from a GPT-5.x model must not reach the API."""
        filtered = GPT6SolParameters.filter_overrides({"effort": effort, "temperature": 0.7, "top_p": 0.9})
        assert filtered == {"effort": effort}

    @pytest.mark.parametrize("model_name", ["gpt-6-sol", "gpt-6-luna"])
    def test_registered_for_gpt_6_sol_and_luna(self, model_name):
        assert LLM_MODEL_PARAMETERS[model_name] is GPT6SolParameters
