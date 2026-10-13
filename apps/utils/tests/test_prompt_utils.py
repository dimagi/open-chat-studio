import pytest
from django.forms import ValidationError

from apps.utils.prompt import (
    PROMPT_VAR_DESCRIPTIONS,
    PROMPT_VARS_REQUIRING_RESOURCES,
    PromptVars,
    get_prompt_variables,
    validate_prompt_variables,
)

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


def test_every_offered_prompt_var_has_a_description():
    """The v2 discovery API looks each one up by label, so a gap is a KeyError at request time."""
    missing = sorted(
        {
            entry["label"]
            for accessor in (
                PromptVars.get_all_prompt_vars,
                PromptVars.get_router_prompt_vars,
                PromptVars.get_jinja_vars,
            )
            for entry in accessor()
            if entry["label"] not in PROMPT_VAR_DESCRIPTIONS
        }
    )
    assert not missing, f"Add these to PROMPT_VAR_DESCRIPTIONS in apps/utils/prompt.py: {missing}"


@pytest.mark.parametrize(
    ("prompt", "variables"),
    [
        pytest.param("No variables here", set(), id="none"),
        pytest.param("{participant_data} and {source_material}", {"participant_data", "source_material"}, id="plain"),
        pytest.param("{participant_data.name} {temp_state[key]}", {"participant_data", "temp_state"}, id="nested"),
        pytest.param('JSON like {{"ok": true}}', set(), id="escaped-braces"),
    ],
)
def test_get_prompt_variables(prompt, variables):
    assert get_prompt_variables(prompt) == variables


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param("Unclosed {participant_data", id="unbalanced"),
        pytest.param("{participant_data!r}", id="conversion"),
    ],
)
def test_get_prompt_variables_rejects_a_prompt_that_does_not_parse(prompt):
    with pytest.raises(ValidationError):
        get_prompt_variables(prompt)


@pytest.mark.parametrize(
    ("offered", "accepted"),
    [
        pytest.param(PromptVars.get_all_prompt_vars, PromptVars.llm_node_vars, id="llm-node"),
        pytest.param(PromptVars.get_router_prompt_vars, PromptVars.router_node_vars, id="router-node"),
    ],
)
def test_the_builder_offers_exactly_the_variables_validation_accepts(offered, accepted):
    assert {option["value"] for option in offered()} == accepted()
