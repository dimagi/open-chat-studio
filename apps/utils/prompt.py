from typing import Any

from django.db import models
from django.forms import ValidationError
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from apps.experiments.models import AgentTools


class PromptVars(models.TextChoices):
    PARTICIPANT_DATA = "participant_data"
    SOURCE_MATERIAL = "source_material"
    CURRENT_DATETIME = "current_datetime"
    MEDIA = "media"


PROMPT_VARS_REQUIRED_BY_TOOL = {
    AgentTools.DELETE_REMINDER: [PromptVars.PARTICIPANT_DATA],
    AgentTools.MOVE_SCHEDULED_MESSAGE_DATE: [PromptVars.PARTICIPANT_DATA, PromptVars.CURRENT_DATETIME],
    AgentTools.ONE_OFF_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.RECURRING_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.UPDATE_PARTICIPANT_DATA: [PromptVars.PARTICIPANT_DATA],
}

PROMPT_VAR_CONTEXT_VAR_MAP = {
    PromptVars.SOURCE_MATERIAL: "source_material",
    PromptVars.MEDIA: "collection",
}


def _inspect_prompt(context: str, prompt_key) -> tuple[set, str]:
    """Inspects the prompt text to extract the variables used in it."""
    prompt_text = context.get(prompt_key, "")
    try:
        promp_vars = {get_root_var(var) for var in PromptTemplate.from_template(prompt_text).input_variables}
        return promp_vars, prompt_text
    except ValueError as e:
        raise ValidationError({prompt_key: f"Invalid format in prompt: {e}"})


def validate_prompt_variables(context, prompt_key: str, known_vars: set):
    """Ensures that the variables expected by the prompt has values and that only those in `known_vars` are allowed
    to be used, otherwise a `ValidationError` is thrown.
    """
    prompt_variables, prompt_text = _inspect_prompt(context, prompt_key)

    unknown = prompt_variables - known_vars
    if unknown:
        raise ValidationError({prompt_key: f"Prompt contains unknown variables: {', '.join(unknown)}"})

    _ensure_component_variables_are_present(context, prompt_variables, prompt_key)
    _ensure_variable_components_are_present(context, prompt_variables, prompt_key)

    for var in prompt_variables:
        if prompt_text.count(f"{{{var}}}") > 1:
            raise ValidationError({prompt_key: f"Variable {var} is used more than once."})


def _ensure_component_variables_are_present(context: dict, prompt_variables: set, prompt_key: str):
    """Ensure that linked components are referenced by the prompt"""
    for prompt_var, context_var in PROMPT_VAR_CONTEXT_VAR_MAP.items():
        if context.get(context_var) and prompt_var not in prompt_variables:
            raise ValidationError({prompt_key: f"Prompt expects {prompt_var} variable."})

    if tools := context.get("tools", []):
        required_prompt_variables = []
        for tool_name in tools:
            required_prompt_variables.extend(PROMPT_VARS_REQUIRED_BY_TOOL[AgentTools(tool_name)])

        missing_vars = set(required_prompt_variables) - prompt_variables
        if missing_vars:
            raise ValidationError(
                {prompt_key: f"Tools require {', '.join(missing_vars)}. Please include them in your prompt."}
            )


def _ensure_variable_components_are_present(context: dict, prompt_variables: set, prompt_key: str):
    """Ensures that all variables in the prompt are referencing valid values."""
    for prompt_var, context_var in PROMPT_VAR_CONTEXT_VAR_MAP.items():
        if prompt_var in prompt_variables and context.get(context_var) is None:
            raise ValidationError({prompt_key: f"{prompt_var} variable is specified, but {context_var} is missing"})


def get_root_var(var: str) -> str:
    """Returns the root variable name from a nested variable name.
    Only `participant_data` is supported.

    See `apps.service_providers.llm_service.prompt_context.SafeAccessWrapper`
    """
    if not var.startswith(PromptVars.PARTICIPANT_DATA.value):
        return var

    var_root = var.split(".")[0]
    if var_root == var and "[" in var:
        var_root = var[: var.index("[")]
    return var_root


class OcsPromptTemplate(ChatPromptTemplate):
    """Custom prompt template that supports nested variables.

    See `apps.service_providers.llm_service.prompt_context.SafeAccessWrapper`
    """

    def _validate_input(self, inner_input: Any) -> dict:
        if not isinstance(inner_input, dict):
            if len(self.input_variables) == 1:
                var_name = self.input_variables[0]
                inner_input = {var_name: inner_input}

            else:
                msg = f"Expected mapping type as input to {self.__class__.__name__}. " f"Received {type(inner_input)}."
                raise TypeError(msg)

        root_vars = {get_root_var(var) for var in self.input_variables}
        missing = root_vars.difference(inner_input)
        if missing:
            msg = (
                f"Prompt contains variables {missing} that are not provided. "
                f"Available variables are: {list(inner_input.keys())}."
            )
            example_key = missing.pop()
            msg += (
                f" Note: if you intended '{{{example_key}}}' to be part of the string"
                " and not a variable, please escape it with double curly braces like: "
                f"'{{{{{example_key}}}}}'."
            )
            raise KeyError(msg)
        return inner_input
