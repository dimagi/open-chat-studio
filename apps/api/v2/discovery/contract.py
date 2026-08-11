"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.
The maps here are what make that hold: they rename the option keys the builder spells differently,
hide the ones a client has no use for, and state the cross-param rules the builder enforces in JS
instead. See ADR-0051.
"""

# `jinja_node` is named for the builder's jinja-template widget rather than for the param that reads it,
# so it is renamed on the API surface: a param's options always live under a key of the same name.
OPTIONS_KEY_RENAMES = {
    "jinja_node": "prompt_variables",
}

# Option lists the API does not serve. The autocomplete keys hold the entries the builder offers
# while someone types an LLM or router prompt -- a dropdown's worth of names, with the node's real
# prompt contract (which variables it accepts, what each holds) living in the param's own
# description. `assistant` is read by one deprecated node type, which the API does not list, so
# nothing it does serve can consume the list. The rest belong to a param marked `api_exclude`.
HIDDEN_OPTION_KEYS = frozenset(
    {
        "text_editor_autocomplete_vars_llm_node",
        "text_editor_autocomplete_vars_router_node",
        "assistant",
        "mcp_tools",
    }
)

# Params the builder renders with a bespoke widget rather than the generic `select`, so it declares
# no `ui:optionsSource` for them. Their options key is the param name, as everywhere else.
IMPLIED_OPTION_KEYS = frozenset({"llm_provider_id", "llm_provider_model_id", "tool_config", "synthetic_voice_id"})

# Both the model and the provider carry a `type` ("openai", "anthropic", ...) and the two must agree;
# `get_node_default_values` silently relies on this when it picks the pair a new node starts with.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`. Both of
# these option keys are dicts keyed by provider type rather than flat lists.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}

# `ui:*` property keys that carry meaning for a client, renamed out of the builder's vocabulary.
# Everything else `ui:*` is presentation (`ui:widget`, `ui:rows`, `ui:onShowDefault`) or duplicates
# something the client already has (`ui:enumLabels` restates `enum`, `ui:optionsSource` restates the
# param name) and is dropped.
UI_KEY_TRANSLATIONS = {
    "ui:visibleWhen": "applies_when",
    "ui:flagRequired": "requires_feature_flag",
}

SINGLE_OUTPUT = {
    "kind": "single",
    "handles": ["output"],
    "description": "One output, handle `output`. Every edge leaving this node uses it.",
}
PER_KEYWORD_OUTPUT = {
    "kind": "per_keyword",
    "handles": None,
    "handle_pattern": "output_{index}",
    "description": (
        "One output per entry in `keywords`: entry `i` is served by handle `output_i`, so an edge "
        "leaving this node must set `source_handle` to match the route it represents. The run ends "
        "if the chosen handle has no edge."
    ),
}
