"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.
The maps here are what make that hold: they rename the option keys the builder spells differently,
hide the ones a client has no use for, and state the cross-param rules the builder enforces in JS
instead. See ADR-0051.
"""

# The prompt-variable lists are the one documented exception to "a param's options live under a key of
# the same name". Three node families have a prompt-shaped param -- `template_string` on the template
# nodes, `prompt` on the LLM and router nodes -- and each accepts a *different* set of variables, so a
# key per param name cannot work: the payload is one flat dict, and `prompt` would collide with
# `prompt`. The key names the prompt flavour instead. The builder's own names for these lists are
# widget names, which never reach a client.
OPTIONS_KEY_RENAMES = {
    "jinja_node": "prompt_variables",
    "text_editor_autocomplete_vars_llm_node": "llm_prompt_variables",
    "text_editor_autocomplete_vars_router_node": "router_prompt_variables",
}

# Option lists the API does not serve. `assistant` is read by one deprecated node type, which the API
# does not list, so nothing it does serve can consume the list. `mcp_tools` belongs to a param marked
# `api_exclude`.
HIDDEN_OPTION_KEYS = frozenset({"assistant", "mcp_tools"})

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
