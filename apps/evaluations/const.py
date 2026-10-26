import re
from datetime import timedelta
from string import Formatter

PREVIEW_SAMPLE_SIZE = 10

# How long the results page waits for a completed run's finalization before settling on the
# aggregates it can see. Finalization is its own task (see ADR-0047), so a lost dispatch or a
# dead worker would otherwise leave the page polling for aggregates that are never coming.
FINALIZATION_GRACE = timedelta(minutes=10)

# The results table shows only failed rows when this query parameter is "1".
FAILED_FILTER_PARAM = "failed"

# Caps the characters of each generation record field substituted into an evaluator prompt.
GENERATION_PROMPT_VARIABLE_MAX_LENGTH = 10_000

EVALUATION_RUN_FIXED_HEADERS = [
    "id",
    "session",
    "source_session",
    "source_experiment_id",
    "Dataset Input",
    "Dataset Output",
    "Generated Response",
]


# Variables an evaluator can interpolate, and the single source of truth for the prompt field
# description, the validator, and the form's autocomplete and hints. Add one here and in the
# corresponding evaluator's ``run()`` -- nowhere else. ``dotted`` marks a dict, useful only with
# a trailing key. Keyed by ``EvaluationMode`` value, since ``models`` imports this module.
EVALUATOR_PROMPT_VARIABLES = {
    "message": [
        ("input.content", False),
        ("output.content", False),
        ("context", True),
        ("participant_data", True),
        ("session_state", True),
        ("full_history", False),
        ("generated_response", False),
        # JSON strings of the generation record (``GenerationRecord.prompt_variables``), capped at
        # ``GENERATION_PROMPT_VARIABLE_MAX_LENGTH``, so no sub-key is addressable. The validator
        # accepts exactly these names, with no conversion or format spec.
        ("generation.node_outputs", False),
        ("generation.path", False),
        ("generation.message_tags", False),
        ("generation.session_tags", False),
        ("generation.participant_data_diff", False),
        ("generation.llm_calls", False),
        ("generation.tool_calls", False),
    ],
    "session": [
        ("context", True),
        ("participant_data", True),
        ("session_state", True),
        ("full_history", False),
    ],
}

# Roots only (``input``, not ``input.content``), so the validator keeps accepting unadvertised
# sub-keys like ``{input.role}``. Params carry no evaluation mode, so it cannot be mode-aware.
ALL_EVALUATOR_PROMPT_VARIABLE_ROOTS = {
    name.split(".")[0] for variables in EVALUATOR_PROMPT_VARIABLES.values() for name, _ in variables
}


GENERATION_PROMPT_VARIABLE_NAMES = [
    name for name, _ in EVALUATOR_PROMPT_VARIABLES["message"] if name.split(".")[0] == "generation"
]


def evaluator_prompt_variable_names(evaluation_mode: str) -> list[str]:
    """Autocomplete entries for `evaluation_mode`, falling back to message mode."""
    variables = EVALUATOR_PROMPT_VARIABLES.get(evaluation_mode, EVALUATOR_PROMPT_VARIABLES["message"])
    return [name for name, _ in variables]


def evaluator_prompt_variable_hints(evaluation_mode: str) -> list[str]:
    """Display forms for `evaluation_mode`, e.g. ``{context.[key]}``, for the form's help text."""
    variables = EVALUATOR_PROMPT_VARIABLES.get(evaluation_mode, EVALUATOR_PROMPT_VARIABLES["message"])
    return [f"{{{name}.[key]}}" if dotted else f"{{{name}}}" for name, dotted in variables]


def prompt_variable_roots(prompt: str) -> set[str]:
    """Root names of the format fields in `prompt`, e.g. ``{context[key]}`` -> ``context``.

    Escaped literal braces yield no field. A malformed template yields nothing, so callers
    surface it as a missing variable rather than an unhandled ``ValueError``.
    """
    try:
        fields = [field for _, field, _, _ in Formatter().parse(prompt) if field]
    except ValueError:
        return set()
    return {re.split(r"[.\[]", field, maxsplit=1)[0] for field in fields}


def _field_as_written(field: str, format_spec: str, conversion: str | None) -> str:
    return field + (f"!{conversion}" if conversion else "") + (f":{format_spec}" if format_spec else "")


def unknown_generation_variables(prompt: str) -> set[str]:
    """Format fields rooted at ``generation`` that are not an advertised name or carry a conversion or format spec.

    Each is returned as written, e.g. ``generation.message_tags!r``. A malformed template yields
    nothing, as in `prompt_variable_roots`.
    """
    try:
        parsed = [(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(prompt) if field]
    except ValueError:
        return set()
    return {
        _field_as_written(field, spec or "", conversion)
        for field, spec, conversion in parsed
        if re.split(r"[.\[]", field, maxsplit=1)[0] == "generation"
        and (field not in GENERATION_PROMPT_VARIABLE_NAMES or spec or conversion)
    }
