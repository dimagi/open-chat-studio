from string import Formatter
from typing import Any

from django.db import models
from django.forms import ValidationError
from langchain_core.prompts import ChatPromptTemplate

from apps.experiments.models import AgentTools


class PromptVars(models.TextChoices):
    PARTICIPANT_DATA = "participant_data"
    SOURCE_MATERIAL = "source_material"
    CURRENT_DATETIME = "current_datetime"


PROMPT_VARS_REQUIRED_BY_TOOL = {
    AgentTools.DELETE_REMINDER: [PromptVars.PARTICIPANT_DATA],
    AgentTools.MOVE_SCHEDULED_MESSAGE_DATE: [PromptVars.PARTICIPANT_DATA, PromptVars.CURRENT_DATETIME],
    AgentTools.ONE_OFF_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.RECURRING_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.UPDATE_PARTICIPANT_DATA: [PromptVars.PARTICIPANT_DATA],
}


def validate_prompt_variables(form_data, prompt_key: str, known_vars: set):
    """Ensures that the variables expected by the prompt has values and that only those in `known_vars` are allowed
    to be used, otherwise a `ValidationError` is thrown.
    """
    prompt_text = form_data.get(prompt_key, "")
    if not prompt_text:
        return set()

    prompt_variables = set()
    try:
        for literal, field_name, format_spec, conversion in Formatter().parse(prompt_text):
            if field_name is not None:
                if format_spec or conversion:
                    conversion = f"!{conversion}" if conversion else ""
                    format_spec = f":{format_spec}" if format_spec else ""
                    variable = f"{{{field_name}{conversion}{format_spec}}}"
                    bad_part = f"{conversion}{format_spec}"
                    raise ValidationError(
                        {prompt_key: f"Invalid prompt variable '{variable}'. Remove the '{bad_part}'."}
                    )
                prompt_variables.add(get_root_var(field_name))
    except ValueError as e:
        raise ValidationError({prompt_key: f"Invalid format in prompt: {e}"})

    unknown = prompt_variables - known_vars
    if unknown:
        raise ValidationError({prompt_key: f"Prompt contains unknown variables: {', '.join(unknown)}"})

    if not form_data.get("source_material") and "source_material" in prompt_variables:
        raise ValidationError({prompt_key: "Prompt expects source_material but it is not provided."})
    elif form_data.get("source_material") and "source_material" not in prompt_variables:
        raise ValidationError({prompt_key: "source_material variable expected since source material is specified"})

    if tools := form_data.get("tools", []):
        required_prompt_variables = []
        for tool_name in tools:
            required_prompt_variables.extend(PROMPT_VARS_REQUIRED_BY_TOOL[AgentTools(tool_name)])

        missing_vars = set(required_prompt_variables) - prompt_variables
        if missing_vars:
            raise ValidationError(
                {prompt_key: f"Tools require {', '.join(missing_vars)}. Please include them in your prompt."}
            )

    for var in prompt_variables:
        if prompt_text.count(f"{{{var}}}") > 1:
            raise ValidationError({prompt_key: f"Variable {var} is used more than once."})


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
