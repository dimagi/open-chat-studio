from datetime import timedelta

PREVIEW_SAMPLE_SIZE = 10

# How long the results page waits for a completed run's finalization before settling on the
# aggregates it can see. Finalization is its own task (see ADR-0047), so a lost dispatch or a
# dead worker would otherwise leave the page polling for aggregates that are never coming.
FINALIZATION_GRACE = timedelta(minutes=10)

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


def evaluator_prompt_variable_names(evaluation_mode: str) -> list[str]:
    """Autocomplete entries for `evaluation_mode`, falling back to message mode."""
    variables = EVALUATOR_PROMPT_VARIABLES.get(evaluation_mode, EVALUATOR_PROMPT_VARIABLES["message"])
    return [name for name, _ in variables]


def evaluator_prompt_variable_hints(evaluation_mode: str) -> list[str]:
    """Display forms for `evaluation_mode`, e.g. ``{context.[key]}``, for the form's help text."""
    variables = EVALUATOR_PROMPT_VARIABLES.get(evaluation_mode, EVALUATOR_PROMPT_VARIABLES["message"])
    return [f"{{{name}.[key]}}" if dotted else f"{{{name}}}" for name, dotted in variables]
