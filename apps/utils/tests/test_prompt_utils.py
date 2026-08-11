import pytest
from django.forms import ValidationError

from apps.utils.prompt import (
    PROMPT_VAR_DESCRIPTIONS,
    PROMPT_VARS_REQUIRING_RESOURCES,
    PromptVars,
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


class TestPromptVarDescriptions:
    """The v2 discovery API serves these descriptions to an LLM agent in place of the redundant
    ``value``, so a variable without one is a KeyError at request time, not a cosmetic gap."""

    def test_every_offered_variable_has_a_description(self):
        """All three prompt-variable lists are served, so all three need full coverage -- the mirror
        of `test_no_description_is_orphaned`, which already spans them."""
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

    def test_no_description_is_orphaned(self):
        """The reverse guard: a description for a variable nothing offers is dead weight."""
        offered = {
            entry["label"]
            for accessor in (
                PromptVars.get_all_prompt_vars,
                PromptVars.get_router_prompt_vars,
                PromptVars.get_jinja_vars,
            )
            for entry in accessor()
        }
        assert not set(PROMPT_VAR_DESCRIPTIONS) - offered

    def test_builder_payload_still_carries_value(self):
        """The pipeline builder's autocomplete widget reads ``value``. Only the API swaps it out,
        so these accessors must keep emitting it."""
        for entry in PromptVars.get_all_prompt_vars():
            assert entry["value"] == entry["label"]
            assert "description" not in entry
