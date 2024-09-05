from django.forms import ValidationError
from langchain_core.prompts import PromptTemplate


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

    if form_data.get("tools"):
        tools_need = {"current_datetime", "participant_data"}
        missing_vars = tools_need - prompt_variables
        if missing_vars:
            raise ValidationError(
                {prompt_key: f"Tools require {', '.join(missing_vars)}. Please include them in your prompt."}
            )

    for var in prompt_variables:
        if prompt_text.count(f"{{{var}}}") > 1:
            raise ValidationError({prompt_key: f"Variable {var} is used more than once."})
