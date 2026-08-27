"""The contract between `/pipeline/nodes/` and `/pipeline/options/`: what a node schema cannot state
for itself.

Which params the API withholds is declared on the pydantic `Field` instead -- see
`UiSchema.api_exclude`.
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
    """The option list a resource param draws from, matched by name.

    Some keys keep the param's ``_id``/``_ids`` suffix (``synthetic_voice_id``), others drop it
    (``collection_id`` -> ``collection``), so both spellings are tried.

    ``None`` for a mirrored param no option list serves: nothing offers it, so no write can name it
    either. Left out rather than raised over, so an unrelated ``SET_NULL`` FK on ``Node`` cannot
    break this import.
    """
    for candidate in (param, param.removesuffix("_ids"), param.removesuffix("_id")):
        if candidate in OptionsSource.__members__:
            return OptionsSource[candidate]
    return None


#: The option lists a team could be denied a value from, so a param drawing from one is checked
#: before it is written. Keyed on the list, not the param: the list is what decides the permitted
#: values. Derived from the resource mirror so a new resource relation on ``Node`` needs no edit
#: here; ``tools`` is named because nothing in the database backs it.
PARAMETER_OPTION_SOURCES: frozenset[OptionsSource] = frozenset(
    {source for param in Node.resource_param_names() if (source := _resource_source(param))} | {OptionsSource.tools}
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
