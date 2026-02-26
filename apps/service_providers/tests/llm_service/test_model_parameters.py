import pytest
from pydantic import ValidationError

from apps.service_providers.llm_service.model_parameters import GPT52Parameters, get_schema


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
