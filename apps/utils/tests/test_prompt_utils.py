import pytest
from django.forms import ValidationError

from apps.utils.prompt import PROMPT_VARS_REQUIRING_RESOURCES, PromptVars, validate_prompt_variables

_context = {
    "source_material": 1,
    "collection": 1,
}


class TestValidatePromptVariables:
    def test_success(self):
        context = {"source_material": 1, "prompt": "Test prompt with {source_material}"}
        known_vars = {PromptVars.SOURCE_MATERIAL}
        validate_prompt_variables(context, prompt_key="prompt", known_vars=known_vars)

    def test_unknown_variable(self):
        context = {"prompt": "Test prompt with {unknown_var}"}
        known_vars = set(PromptVars.values)
        with pytest.raises(ValidationError, match="Prompt contains unknown variables: unknown_var"):
            validate_prompt_variables(context, prompt_key="prompt", known_vars=known_vars)

    def test_missing_variable(self):
        for prompt_var in PROMPT_VARS_REQUIRING_RESOURCES:
            context = {prompt_var: 1, "prompt": "Test prompt"}

            with pytest.raises(ValidationError, match=f"Prompt expects {prompt_var} variable."):
                validate_prompt_variables(context, prompt_key="prompt", known_vars=set(PromptVars.values))

    def test_missing_component(self):
        for prompt_var in PROMPT_VARS_REQUIRING_RESOURCES:
            context = {"prompt": f"Test prompt with {{{prompt_var}}}"}
            with pytest.raises(
                ValidationError, match=f"{prompt_var} variable is specified, but {prompt_var} is missing"
            ):
                validate_prompt_variables(context, prompt_key="prompt", known_vars=set(PromptVars.values))
