from django.forms import ValidationError
from langchain_core.prompts import PromptTemplate


def validate_prompt_variables(form_data, prompt_key: str, known_vars: dict):
    """Ensures that the variables expected by the prompt has values and that only those in `known_vars` are allowed
    to be used, otherwise a `ValidationError` is thrown.
    """
    prompt_text = form_data[prompt_key]
    prompt_variables = set(PromptTemplate.from_template(prompt_text).input_variables)
    available_variables = set(["participant_data", "current_datetime"])

    if form_data.get("source_material"):
        available_variables.add("source_material")

    if form_data["tools"]:
        if "current_datetime" not in prompt_variables:
            available_variables.remove("current_datetime")
        # if there are "tools" then current_datetime is always required
        prompt_variables.add("current_datetime")

    missing_vars = prompt_variables - available_variables
    if missing_vars:
        errors = []
        unknown_vars = missing_vars - known_vars
        if unknown_vars:
            errors.append("Prompt contains unknown variables: " + ", ".join(unknown_vars))
            missing_vars -= unknown_vars
        if missing_vars:
            errors.append(
                f"Prompt expects {', '.join(missing_vars)} but it is not provided. See the help text on variable "
                "usage."
            )
        raise ValidationError({prompt_key: errors})

    for prompt_var in ["{source_material}", "{participant_data}"]:
        if prompt_text.count(prompt_var) > 1:
            error_msg = f"Multiple {prompt_var} variables found in the prompt. You can only use it once"
            raise ValidationError({prompt_key: error_msg})
