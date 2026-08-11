"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.

What lives here is only what a node schema cannot state for itself: the prompt-variable keys, whose
one param name maps to three different vocabularies, the one list no param reads, and the cross-param
rules the builder enforces in JS instead. Which params the API withholds is declared on the pydantic
`Field` -- see `UiSchema.api_exclude`. See ADR-0051.
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

# The one option list no node param reads. `synthetic_voice_id` entries carry the `provider_id` they
# have to match, and this is the list that resolves it -- so it is served even though the whitelist
# derived from the node schemas cannot discover it. Everything else served must be reachable from a
# param, which `test_every_key_served_is_read_by_some_listed_node_type` enforces.
API_ONLY_OPTION_KEYS = frozenset({"voice_provider_id"})

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
