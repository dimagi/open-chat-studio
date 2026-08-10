"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

One rule, no exceptions: a param's ``options_source`` names the key in `/pipeline/options/` holding
its permitted values. The maps here are what make that true where the builder never declared the
link, and what state the cross-param rules it enforces in JS instead. See ADR-0051.
"""

# `OptionsSource` names that are not snake_case. The builder's JS reads these keys verbatim
# (assets/javascript/apps/pipeline/nodes/widgets.tsx), so they are renamed on the API surface only.
OPTIONS_KEY_RENAMES = {
    "LlmProviderId": "llm_provider_id",
    "LlmProviderModelId": "llm_provider_model_id",
    "VoiceProviderId": "voice_provider_id",
}

# Params whose values come from an options key the builder never declared, because it renders them
# with a bespoke widget rather than the generic `select`. Without these the join has four exceptions
# and the agent has to guess them from naming.
IMPLIED_OPTIONS_SOURCE = {
    "llm_provider_id": "llm_provider_id",
    "llm_provider_model_id": "llm_provider_model_id",
    "tool_config": "built_in_tools_config",
    "synthetic_voice_id": "synthetic_voice_id",
}

# Both the model and the provider carry a `type` ("openai", "anthropic", ...) and the two must agree;
# `get_node_default_values` silently relies on this when it picks the pair a new node starts with.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`. Both of
# these option keys are dicts keyed by provider type rather than flat lists.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}

# `ui:*` property keys that carry meaning for an agent, renamed out of the builder's vocabulary.
# Everything else `ui:*` is presentation (`ui:widget`, `ui:rows`, `ui:onShowDefault`) or duplicates
# the JSON Schema (`ui:enumLabels` restates `enum`) and is dropped.
UI_KEY_TRANSLATIONS = {
    "ui:optionsSource": "options_source",
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
