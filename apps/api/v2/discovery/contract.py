"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.
What lives here is what a node schema cannot state for itself. Which params the API withholds is
declared on the pydantic `Field` instead -- see `UiSchema.api_exclude`.
"""

from apps.pipelines.nodes.base import OptionsSource

# Model and provider each carry a `type` ("openai", "anthropic", ...) and the two must agree.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}

# Sources whose entries are not values a param may hold, so a write must not check a param
# against them: the three variable lists document what a template may interpolate, and the two
# tool keys nest their lists inside a dict keyed by provider type. Stated as the exceptions rather
# than as an allowlist so a source added later is checked by default.
NON_REFERENCE_OPTION_SOURCES = frozenset(
    {
        OptionsSource.llm_prompt_variables,
        OptionsSource.router_prompt_variables,
        OptionsSource.template_variables,
        OptionsSource.built_in_tools,
        OptionsSource.tool_config,
    }
)

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
