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
    MEDIA = "media"
    COLLECTION_INDEX_SUMMARIES = "collection_index_summaries"

    @staticmethod
    def pipeline_extra_known_vars() -> set[str]:
        return {"temp_state", "session_state"}

    @staticmethod
    def get_all_prompt_vars() -> list[dict]:
        base_vars = [v.value for v in PromptVars]
        all_vars = base_vars + list(PromptVars.pipeline_extra_known_vars())
        return [{"label": v, "value": v} for v in all_vars]

    @staticmethod
    def get_router_prompt_vars() -> list[dict]:
        prompt_vars = {"participant_data"} | PromptVars.pipeline_extra_known_vars()
        return [{"label": v, "value": v} for v in prompt_vars]

    @staticmethod
    def get_jinja_vars() -> list[dict]:
        vars_ = [
            "input",
            "node_inputs",
            "temp_state",
            "session_state",
            "participant_data",
            "participant_details",
            "participant_schedules",
            "input_message_id",
            "input_message_url",
        ]
        return [{"label": v, "value": v} for v in vars_]


# What each template variable holds and when to reach for it. The pipeline builder shows these
# variables as bare autocomplete entries -- a human infers the meaning from the name -- but the v2
# discovery API serves them to an LLM agent that has no such context, so it gets these instead of
# the (identical to the label) value. Every variable `PromptVars.get_jinja_vars` returns must have
# an entry; TestPromptVarDescriptions in apps/utils/tests/test_prompt_utils.py enforces that.
PROMPT_VAR_DESCRIPTIONS = {
    "participant_data": (
        "The participant's stored data, addressed by key -- `participant_data.name`. Use it to "
        "personalise a response. In LLM prompts it also carries their `scheduled_messages`, and a "
        "key that doesn't exist renders empty instead of failing."
    ),
    "temp_state": (
        "Scratch state for the current run only, holding `user_input`, `outputs` (keyed by node "
        "name) and `attachments`. Use it to pass data between nodes while handling one message. "
        "Discarded when the run ends -- `session_state` is the durable equivalent."
    ),
    "session_state": (
        "State that survives for the whole session. Use it to remember something between messages, "
        "such as a preference the participant stated earlier. `temp_state` is the per-run equivalent."
    ),
    "source_material": (
        "The full text of the source material chosen in `source_material_id`. Use it to give the "
        "model reference content to answer from. Renders empty if no source material is set."
    ),
    "media": (
        "One line per file in the collection chosen in `collection_id`, each carrying the file's "
        "id, content type and summary. Use it to let the model refer to the files it can discuss. "
        "Renders empty if no collection is set."
    ),
    "collection_index_summaries": (
        "One line per index in `collection_index_ids`, each carrying the index's id, name and "
        "summary. Use it to let the model choose which index to search. Renders empty if none are "
        "selected."
    ),
    "current_datetime": (
        "The current date and time in the participant's timezone. Use it for anything "
        "time-relative, such as scheduling a reminder. Rendered to day precision inside the "
        "cacheable system prompt, with the precise time supplied on the latest message instead."
    ),
    "input": "The text passed into this node from the preceding one.",
    "node_inputs": (
        "The inputs to this node as a list, for when more than one node feeds it. Use it to combine several branches."
    ),
    "participant_details": (
        "The participant's `identifier` and `platform`. Use it when the message should reference "
        "who they are or where they're talking to you from."
    ),
    "participant_schedules": ("The participant's scheduled messages for this bot, as a list, including inactive ones."),
    "input_message_id": ("The database id of the incoming message. Use it to correlate with traces or the API."),
    "input_message_url": "A link to the incoming message in the web UI.",
}


PROMPT_VARS_REQUIRED_BY_TOOL = {
    AgentTools.DELETE_REMINDER: [PromptVars.PARTICIPANT_DATA],
    AgentTools.MOVE_SCHEDULED_MESSAGE_DATE: [PromptVars.PARTICIPANT_DATA, PromptVars.CURRENT_DATETIME],
    AgentTools.ONE_OFF_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.RECURRING_REMINDER: [PromptVars.CURRENT_DATETIME],
    AgentTools.UPDATE_PARTICIPANT_DATA: [PromptVars.PARTICIPANT_DATA],
    AgentTools.APPEND_TO_PARTICIPANT_DATA: [PromptVars.PARTICIPANT_DATA],
}

# These prompt variables require resources to be specified by the user
PROMPT_VARS_REQUIRING_RESOURCES = [PromptVars.SOURCE_MATERIAL, PromptVars.MEDIA, PromptVars.COLLECTION_INDEX_SUMMARIES]


def _inspect_prompt(context: str, prompt_key) -> tuple[set, str]:
    """Inspects the prompt text to extract the variables used in it."""
    prompt_variables = set()
    prompt_text = context.get(prompt_key, "")
    try:
        for _literal, field_name, format_spec, conversion in Formatter().parse(prompt_text):
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
        raise ValidationError({prompt_key: f"Invalid format in prompt: {e}"}) from None

    return prompt_variables, prompt_text


def validate_prompt_variables(context, prompt_key: str, known_vars: set):
    """Ensures that the variables expected by the prompt has values and that only those in `known_vars` are allowed
    to be used, otherwise a `ValidationError` is thrown.
    """
    prompt_variables, prompt_text = _inspect_prompt(context, prompt_key)
    tools = context.get("tools", [])

    if not prompt_text and not tools:
        return set()

    unknown = prompt_variables - known_vars
    if unknown:
        raise ValidationError({prompt_key: f"Prompt contains unknown variables: {', '.join(unknown)}"})

    _ensure_component_variables_are_present(context, prompt_variables, prompt_key)
    _ensure_variable_components_are_present(context, prompt_variables, prompt_key)
    _ensure_tool_variables_are_present(prompt_text, prompt_variables, tools, prompt_key)

    for var in prompt_variables:
        if prompt_text.count(f"{{{var}}}") > 1:
            raise ValidationError({prompt_key: f"Variable {var} is used more than once."})


def _ensure_tool_variables_are_present(prompt_text, prompt_variables, tools, prompt_key):
    if not tools:
        return

    required_prompt_variables = []
    for tool_name in tools:
        tool_vars = PROMPT_VARS_REQUIRED_BY_TOOL.get(AgentTools(tool_name)) or {}
        required_prompt_variables.extend(tool_vars)
    missing_vars = set(required_prompt_variables) - prompt_variables
    if missing_vars:
        raise ValidationError(
            {prompt_key: f"Tools require {', '.join(missing_vars)}. Please include them in your prompt."}
        )
    if not prompt_text and required_prompt_variables:
        raise ValidationError({prompt_key: f"Tools {tools} require a prompt with variables, but the prompt is empty."})


def _ensure_component_variables_are_present(context: dict, prompt_variables: set, prompt_key: str):
    """Ensure that linked components are referenced by the prompt"""
    for prompt_var in PROMPT_VARS_REQUIRING_RESOURCES:
        if context.get(prompt_var) and prompt_var not in prompt_variables:
            raise ValidationError({prompt_key: f"Prompt expects {prompt_var} variable."})


def _ensure_variable_components_are_present(context: dict, prompt_variables: set, prompt_key: str):
    """Ensures that all variables in the prompt are referencing valid values."""
    for prompt_var in PROMPT_VARS_REQUIRING_RESOURCES:
        if prompt_var in prompt_variables and context.get(prompt_var) is None:
            raise ValidationError({prompt_key: f"{prompt_var} variable is specified, but {prompt_var} is missing"})

    return prompt_variables


def get_root_var(var: str) -> str:
    """Returns the root variable name from a nested variable name.

    See `apps.service_providers.llm_service.prompt_context.SafeAccessWrapper`
    """
    vars_with_nested_data = {PromptVars.PARTICIPANT_DATA.value, "temp_state", "session_state"}
    if not any(var.startswith(nested) for nested in vars_with_nested_data):
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
                msg = f"Expected mapping type as input to {self.__class__.__name__}. Received {type(inner_input)}."
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
