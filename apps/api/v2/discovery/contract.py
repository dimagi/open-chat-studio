"""The contract between `/pipeline/nodes/` and `/pipeline/options/`.

A param's permitted values live under the `/pipeline/options/` key of the same name --
`source_material_id` draws from `source_material`, `collection_index_ids` from `collection_index`.
What lives here is what a node schema cannot state for itself. Which params the API withholds is
declared on the pydantic `Field` instead -- see `UiSchema.api_exclude`.
"""

from apps.pipelines.models import Node
from apps.pipelines.nodes.base import OptionsSource

# Model and provider each carry a `type` ("openai", "anthropic", ...) and the two must agree.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}


def _resource_source(param: str) -> OptionsSource | None:
    """The option list a resource param draws its values from, found by name.

    An option key is named for the param that reads it, give or take an ``_id``/``_ids`` suffix --
    the convention ``test_every_option_key_is_named_for_the_param_that_reads_it`` holds every key
    to. Some keep the suffix (``synthetic_voice_id``), others drop it (``collection_id`` ->
    ``collection``), so both spellings are tried, the param's own first.

    ``None`` for a mirrored FK that no option list serves: nothing offers it, so no node schema
    declares a source for it and no write can name it either. Left out rather than raised over, so
    that adding an unrelated ``SET_NULL`` FK to ``Node`` cannot break this import --
    ``test_every_mirrored_resource_param_is_checked`` is what says none of the params that *are*
    offered fell through this way.
    """
    for candidate in (param, param.removesuffix("_ids"), param.removesuffix("_id")):
        if candidate in OptionsSource.__members__:
            return OptionsSource[candidate]
    return None


#: The option lists a team could be denied a value from, so a param drawing from one is checked
#: against what the team may reach before it is written. Keyed on the list, not the param, because
#: the list is what decides the permitted values. Derived from the FK mirror so a new resource FK on
#: ``Node`` needs no edit here; ``tools`` and ``custom_actions`` are named because no FK backs them.
#: Most of these name a team's own records; ``tools`` is a fixed vocabulary no team narrows, checked
#: all the same so an unknown name is refused rather than stored.
PARAMETER_OPTION_SOURCES: frozenset[OptionsSource] = frozenset(
    {source for param in Node.resource_param_names() if (source := _resource_source(param))}
    | {OptionsSource.tools, OptionsSource.custom_actions}
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
