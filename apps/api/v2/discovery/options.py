"""Reshaping the builder's option lists into what an agent reads."""

from functools import cache

from apps.pipelines.nodes.base import OptionsSource
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

from .contract import OPTIONS_KEY_RENAMES
from .node_types import _node_types


def _clean_options(value):
    """Strip builder-only affordances from an options payload.

    Two things the editor needs and an agent must not see: placeholder entries with an empty
    ``value`` (a prompt like "Select a topic", not a referenceable id) and ``edit_url`` (a link into
    the Django UI). The walk recurses because ``built_in_tools`` is a dict of lists keyed by provider
    type, not a flat list.
    """
    if isinstance(value, dict):
        return {key: _clean_options(item) for key, item in value.items()}
    if isinstance(value, list):
        return [
            {key: item for key, item in option.items() if key != "edit_url"} if isinstance(option, dict) else option
            for option in value
            if not (isinstance(option, dict) and option.get("value") == "")
        ]
    return value


# The option keys holding prompt/template variables rather than resource ids.
PROMPT_VAR_OPTION_KEYS = (
    OptionsSource.text_editor_autocomplete_vars_llm_node,
    OptionsSource.text_editor_autocomplete_vars_router_node,
    OptionsSource.jinja_node,
)


def _describe_prompt_vars(options: dict) -> dict:
    """Swap each prompt variable's redundant ``value`` for a description of what it does.

    The builder emits these as ``{"label": v, "value": v}`` -- the two are always identical, since
    the value is just the name typed into the template. A human reading an autocomplete dropdown
    infers the rest from the name; an agent cannot, so it gets the description instead. Mutates a
    copy of the list, never ``PROMPT_VAR_DESCRIPTIONS``.
    """
    for key in PROMPT_VAR_OPTION_KEYS:
        if not (entries := options.get(key)):
            continue
        options[key] = [
            {"label": entry["label"], "description": PROMPT_VAR_DESCRIPTIONS[entry["label"]]} for entry in entries
        ]
    return options


def _rename_option_keys(options: dict) -> dict:
    return {OPTIONS_KEY_RENAMES.get(key, key): value for key, value in options.items()}


@cache
def _keys_for_node_type(node_type: str) -> frozenset[str] | None:
    """The option keys a single node type can reference, or ``None`` if no such type is served.

    Everything else in the payload belongs to some other node type, and an agent configuring this
    one pays for it in context for nothing. A known type that references nothing -- ``CodeNode``,
    the structural nodes -- yields an empty set, which is a different answer from ``None``.
    """
    entry = next((node for node in _node_types() if node["type"] == node_type), None)
    if entry is None:
        return None
    properties = entry["schema"]["properties"]
    keys = {prop["options_source"] for prop in properties.values() if prop.get("options_source")}
    if "llm_provider_id" in properties:
        keys.add("default_llm_provider")
    return frozenset(keys)
