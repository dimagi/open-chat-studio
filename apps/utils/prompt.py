from django.db import models
from django.forms import ValidationError
from langchain_core.prompts import PromptTemplate

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
    prompt_text = form_data[prompt_key]
    prompt_variables = set(PromptTemplate.from_template(prompt_text).input_variables)
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
