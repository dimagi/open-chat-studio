"""The option lists a team may draw on, as `/pipeline/options/` serves them.

Split out of the view because the write endpoints check references against exactly these lists:
what the API offers a client and what it accepts back have to be the same set, or discovery is
lying. See ``contract.py`` for how a param name maps onto a key here.
"""

from typing import Any

from apps.pipelines.nodes.base import OptionsSource
from apps.pipelines.nodes.node_metadata import get_node_default_values, get_node_parameter_values
from apps.teams.models import Team
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

# The option lists holding prompt variables rather than referenceable resource ids.
PROMPT_VAR_OPTION_SOURCES = (
    OptionsSource.template_variables,
    OptionsSource.llm_prompt_variables,
    OptionsSource.router_prompt_variables,
)


def options_for_team(team: Team) -> dict:
    """Every option list the team can draw on, with the builder-only affordances stripped.
    Scoping to a node type happens after this.

    Unlike the builders, this serves only what a client may write, so the models the team cannot
    call are left out.
    """
    options = _clean_options(get_node_parameter_values(team=team, usable_models_only=True))
    options["default_llm_provider"] = get_node_default_values(team, usable_models_only=True)
    return _describe_prompt_vars(options)


def _clean_options(value: Any) -> Any:
    """Strip the builder-only affordances off every option list. Recurses -- ``built_in_tools``
    and ``tool_config`` nest their lists inside dicts keyed by provider type."""
    if isinstance(value, dict):
        return {key: _clean_options(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_option(option) for option in value if not _is_placeholder(option)]
    return value


def _is_placeholder(option: Any) -> bool:
    """A builder entry standing in for "nothing chosen". It names no resource to reference."""
    return isinstance(option, dict) and option.get("value") == ""


def _clean_option(option: Any) -> Any:
    """One option entry, with its ``edit_url`` link into the Django UI dropped."""
    if not isinstance(option, dict):
        return option
    return {key: item for key, item in option.items() if key != "edit_url"}


def _describe_prompt_vars(options: dict) -> dict:
    """Swap each prompt variable's redundant ``value`` (always equal to its ``label``) for a
    description of what the variable holds. An uncovered variable is a KeyError here, which
    ``test_every_offered_prompt_var_has_a_description`` guards against."""
    for source in PROMPT_VAR_OPTION_SOURCES:
        if entries := options.get(source):
            options[source] = [
                {"label": entry["label"], "description": PROMPT_VAR_DESCRIPTIONS[entry["label"]]} for entry in entries
            ]
    return options
