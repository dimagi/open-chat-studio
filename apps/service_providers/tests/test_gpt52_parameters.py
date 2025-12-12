import pytest
from pydantic import ValidationError

from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS
from apps.service_providers.llm_service.model_parameters import (
    GPT52Parameters,
    GPT52ReasoningEffortParameter,
    OpenAIVerbosityParameter,
)


class TestGPT52ReasoningEffortParameter:
    """Test that all 5 reasoning effort values are supported."""

    def test_all_effort_values_supported(self):
        """Test that NONE, LOW, MEDIUM, HIGH, and XHIGH are all valid."""
        assert GPT52ReasoningEffortParameter.NONE == "none"
        assert GPT52ReasoningEffortParameter.LOW == "low"
        assert GPT52ReasoningEffortParameter.MEDIUM == "medium"
        assert GPT52ReasoningEffortParameter.HIGH == "high"
        assert GPT52ReasoningEffortParameter.XHIGH == "xhigh"

    def test_has_five_values(self):
        """Test that exactly 5 reasoning effort values are available."""
        assert len(GPT52ReasoningEffortParameter.choices) == 5


class TestGPT52Parameters:
    """Test GPT52Parameters class with conditional parameter validation."""

    def test_default_values(self):
        """Test that default reasoning effort is MEDIUM."""
        params = GPT52Parameters()
        assert params.effort == GPT52ReasoningEffortParameter.MEDIUM
        assert params.verbosity == OpenAIVerbosityParameter.MEDIUM
        assert params.temperature is None
        assert params.top_p is None

    def test_temperature_allowed_with_effort_none(self):
        """Test that temperature can be set when effort is 'none'."""
        params = GPT52Parameters(effort="none", temperature=0.7)
        assert params.temperature == 0.7

    def test_top_p_allowed_with_effort_none(self):
        """Test that top_p can be set when effort is 'none'."""
        params = GPT52Parameters(effort="none", top_p=0.9)
        assert params.top_p == 0.9

    def test_both_params_allowed_with_effort_none(self):
        """Test that both temperature and top_p can be set when effort is 'none'."""
        params = GPT52Parameters(effort="none", temperature=0.7, top_p=0.9)
        assert params.temperature == 0.7
        assert params.top_p == 0.9

    def test_temperature_raises_error_with_effort_low(self):
        """Test that temperature raises error when effort is 'low'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="low", temperature=0.7)
        assert "Temperature can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_temperature_raises_error_with_effort_medium(self):
        """Test that temperature raises error when effort is 'medium'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="medium", temperature=0.7)
        assert "Temperature can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_temperature_raises_error_with_effort_high(self):
        """Test that temperature raises error when effort is 'high'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="high", temperature=0.7)
        assert "Temperature can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_temperature_raises_error_with_effort_xhigh(self):
        """Test that temperature raises error when effort is 'xhigh'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="xhigh", temperature=0.7)
        assert "Temperature can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_top_p_raises_error_with_effort_low(self):
        """Test that top_p raises error when effort is 'low'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="low", top_p=0.9)
        assert "Top P can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_top_p_raises_error_with_effort_medium(self):
        """Test that top_p raises error when effort is 'medium'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="medium", top_p=0.9)
        assert "Top P can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_top_p_raises_error_with_effort_high(self):
        """Test that top_p raises error when effort is 'high'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="high", top_p=0.9)
        assert "Top P can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_top_p_raises_error_with_effort_xhigh(self):
        """Test that top_p raises error when effort is 'xhigh'."""
        with pytest.raises(ValidationError) as exc_info:
            GPT52Parameters(effort="xhigh", top_p=0.9)
        assert "Top P can only be set when reasoning effort is 'none'" in str(exc_info.value)

    def test_all_effort_values_accepted(self):
        """Test that all 5 reasoning effort values are accepted."""
        for effort in ["none", "low", "medium", "high", "xhigh"]:
            params = GPT52Parameters(effort=effort)
            assert params.effort == effort

    def test_verbosity_works_with_all_effort_levels(self):
        """Test that verbosity parameter works correctly with all effort combinations."""
        for effort in ["none", "low", "medium", "high", "xhigh"]:
            for verbosity in ["low", "medium", "high"]:
                params = GPT52Parameters(effort=effort, verbosity=verbosity)
                assert params.effort == effort
                assert params.verbosity == verbosity

    def test_temperature_range_validation(self):
        """Test that temperature is constrained to 0.0-2.0."""
        # Valid temperature values
        GPT52Parameters(effort="none", temperature=0.0)
        GPT52Parameters(effort="none", temperature=1.0)
        GPT52Parameters(effort="none", temperature=2.0)

        # Invalid temperature values should raise ValidationError
        with pytest.raises(ValidationError):
            GPT52Parameters(effort="none", temperature=-0.1)
        with pytest.raises(ValidationError):
            GPT52Parameters(effort="none", temperature=2.1)

    def test_top_p_range_validation(self):
        """Test that top_p is constrained to 0.0-1.0."""
        # Valid top_p values
        GPT52Parameters(effort="none", top_p=0.0)
        GPT52Parameters(effort="none", top_p=0.5)
        GPT52Parameters(effort="none", top_p=1.0)

        # Invalid top_p values should raise ValidationError
        with pytest.raises(ValidationError):
            GPT52Parameters(effort="none", top_p=-0.1)
        with pytest.raises(ValidationError):
            GPT52Parameters(effort="none", top_p=1.1)


class TestGPT52ModelRegistration:
    """Test that GPT-5.2 models are properly registered."""

    def test_gpt52_model_registered(self):
        """Test that gpt-5.2 model is registered in the OpenAI provider."""
        openai_models = DEFAULT_LLM_PROVIDER_MODELS.get("openai", [])
        gpt52_model = next((m for m in openai_models if m.name == "gpt-5.2"), None)
        assert gpt52_model is not None
        assert gpt52_model.token_limit == 400 * 1024  # 400KB
        assert gpt52_model.parameters == GPT52Parameters

    def test_gpt52_pro_model_registered(self):
        """Test that gpt-5.2-pro model is registered in the OpenAI provider."""
        openai_models = DEFAULT_LLM_PROVIDER_MODELS.get("openai", [])
        gpt52_pro_model = next((m for m in openai_models if m.name == "gpt-5.2-pro"), None)
        assert gpt52_pro_model is not None
        assert gpt52_pro_model.token_limit == 400 * 1024  # 400KB
        assert gpt52_pro_model.parameters == GPT52Parameters

    def test_both_models_use_same_parameter_class(self):
        """Test that both models use the same GPT52Parameters class."""
        openai_models = DEFAULT_LLM_PROVIDER_MODELS.get("openai", [])
        gpt52_model = next((m for m in openai_models if m.name == "gpt-5.2"), None)
        gpt52_pro_model = next((m for m in openai_models if m.name == "gpt-5.2-pro"), None)
        assert gpt52_model.parameters == gpt52_pro_model.parameters
        assert gpt52_model.parameters == GPT52Parameters
