"""Two-layer completeness guard (ADR-0025, design decision #4).

Layer 1: every ``OptionsSource`` and ``Widgets`` value is classified as resource-bearing or not —
a new, unclassified signal fails CI rather than silently omitting a resource from the inspect
payload. The classification sets live here (relocated from the deleted ``node_walker``); they are
the reviewed source of truth for "everything else stays in params".

Layer 2: every resource-bearing signal maps to a payload key that has a ``RESOURCE_FIELDS`` entry —
a new resource field with no entry fails CI rather than silently landing in ``params``.
"""

from apps.api.v2.inspect.nodes import RESOURCE_FIELDS
from apps.pipelines.nodes.base import OptionsSource, Widgets

# ``OptionsSource`` values that ARE resource references -> the inspect payload key they render under.
OPTIONS_SOURCE_RESOURCES: dict[OptionsSource, str] = {
    OptionsSource.source_material: "source_material",
    OptionsSource.assistant: "assistant",
    OptionsSource.custom_actions: "custom_actions",
    OptionsSource.collection: "media_collection",
    OptionsSource.collection_index: "indexed_collections",
    # Forward-compat: no current node field uses these as an options_source (voice is signalled by
    # the voice_widget widget), but they denote the ``voice`` key if one ever does.
    OptionsSource.voice_provider_id: "voice",
    OptionsSource.synthetic_voice_id: "voice",
}

# ``OptionsSource`` values that are explicitly NOT resource references (tool enums, autocomplete
# hints, jinja editors) — their fields stay verbatim in ``params``.
OPTIONS_SOURCE_NON_RESOURCES: set[OptionsSource] = {
    OptionsSource.agent_tools,
    OptionsSource.built_in_tools,
    OptionsSource.built_in_tools_config,
    OptionsSource.mcp_tools,
    OptionsSource.jinja_node,
    OptionsSource.text_editor_autocomplete_vars_llm_node,
    OptionsSource.text_editor_autocomplete_vars_router_node,
}

# ``Widgets`` that carry a resource reference -> the payload key they render under.
WIDGET_RESOURCES: dict[Widgets, str] = {
    Widgets.llm_provider_model: "llm",
    Widgets.voice_widget: "voice",
}

# Every other widget is presentational and not, on its own, a resource signal.
WIDGET_NON_RESOURCES: set[Widgets] = {
    Widgets.expandable_text,
    Widgets.code,
    Widgets.toggle,
    Widgets.select,
    Widgets.float,
    Widgets.range,
    Widgets.multiselect,
    Widgets.searchable_multiselect,
    Widgets.none,
    Widgets.history,
    Widgets.keywords,
    Widgets.history_mode,
    Widgets.built_in_tools,
    Widgets.key_value_pairs,
    Widgets.text_editor,
    Widgets.jinja_template,
}


def test_every_options_source_is_classified():
    classified = set(OPTIONS_SOURCE_RESOURCES) | OPTIONS_SOURCE_NON_RESOURCES
    unclassified = set(OptionsSource) - classified
    assert not unclassified, (
        f"Unclassified OptionsSource value(s): {sorted(str(s) for s in unclassified)}. "
        "Register each in OPTIONS_SOURCE_RESOURCES or OPTIONS_SOURCE_NON_RESOURCES in this file "
        "(and add a RESOURCE_FIELDS entry in inspect/nodes.py for resource ones)."
    )


def test_options_source_classification_is_disjoint():
    overlap = set(OPTIONS_SOURCE_RESOURCES) & OPTIONS_SOURCE_NON_RESOURCES
    assert not overlap, f"OptionsSource value(s) classified as both resource and non-resource: {overlap}"


def test_every_widget_is_classified():
    classified = set(WIDGET_RESOURCES) | WIDGET_NON_RESOURCES
    unclassified = set(Widgets) - classified
    assert not unclassified, (
        f"Unclassified Widget value(s): {sorted(str(w) for w in unclassified)}. "
        "Register each in WIDGET_RESOURCES or WIDGET_NON_RESOURCES in this file."
    )


def test_widget_classification_is_disjoint():
    overlap = set(WIDGET_RESOURCES) & WIDGET_NON_RESOURCES
    assert not overlap, f"Widget value(s) classified as both resource and non-resource: {overlap}"


def test_every_resource_signal_has_a_resource_fields_entry():
    """Layer 2: every resource-bearing signal's payload key has a ``RESOURCE_FIELDS`` entry, so a
    newly added resource field can't silently land in ``params``."""
    resource_keys = set(OPTIONS_SOURCE_RESOURCES.values()) | set(WIDGET_RESOURCES.values())
    missing = resource_keys - set(RESOURCE_FIELDS)
    assert not missing, (
        f"Resource signal payload key(s) with no RESOURCE_FIELDS entry: {sorted(missing)}. "
        "Add each to RESOURCE_FIELDS in apps/api/v2/inspect/nodes.py."
    )
