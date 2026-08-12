"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.
What lives here is what a node schema cannot state for itself. Which params the API withholds is
declared on the pydantic `Field` instead -- see `UiSchema.api_exclude`.
"""

# `builder option key -> the key the API serves it under`. These are the builder keys that would
# otherwise break the same-name rule: `agent_tools` holds the values for a param named `tools`, and
# the variable lists have no param named for them at all -- `prompt` on an LLM node and `prompt` on a
# router accept different sets, so those keys name the flavour of text being written instead.
OPTIONS_KEY_RENAMES = {
    "agent_tools": "tools",
    "jinja_node": "template_variables",
    "text_editor_autocomplete_vars_llm_node": "llm_prompt_variables",
    "text_editor_autocomplete_vars_router_node": "router_prompt_variables",
}

# `option key -> the keys a client needs alongside it to make sense of its entries`. A dependency is
# served whenever the key depending on it is, scoped responses included, and no node param reads one
# -- which is why the schema-derived whitelist cannot discover them on its own.
OPTION_KEY_DEPENDENCIES = {
    # Resolves the `provider_id` carried on each `synthetic_voice_id` entry.
    "synthetic_voice_id": frozenset({"voice_provider_id"}),
}

# Served despite no node param reading them.
API_ONLY_OPTION_KEYS = frozenset().union(*OPTION_KEY_DEPENDENCIES.values())

# Model and provider each carry a `type` ("openai", "anthropic", ...) and the two must agree.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}

# `ui:*` keys renamed for the client. Every other namespaced key is dropped.
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
