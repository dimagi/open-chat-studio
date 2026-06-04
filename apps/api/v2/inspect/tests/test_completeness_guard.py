"""Two-layer completeness guard (ADR-0025, design decision #4).

Layer 1: every ``OptionsSource`` and ``Widgets`` value is classified as resource-bearing or not —
a new, unclassified signal fails CI rather than silently omitting a resource from the inspect
payload. The classification sets live here (relocated from the deleted ``node_walker``); they are
the reviewed source of truth for "everything else stays in params".

Layer 2: every resource-bearing signal's backing param field(s) are registered in
``RESOURCE_PARAM_FIELDS`` — a new resource field with no entry fails CI rather than silently
landing in ``params``.
"""

from apps.api.v2.inspect.nodes import RESOURCE_PARAM_FIELDS
from apps.pipelines.nodes.base import OptionsSource, Widgets

# ``OptionsSource`` values that ARE resource references -> the resource param field(s) they populate.
OPTIONS_SOURCE_RESOURCES: dict[OptionsSource, tuple[str, ...]] = {
    OptionsSource.source_material: ("source_material_id",),
    OptionsSource.assistant: ("assistant_id",),
    OptionsSource.custom_actions: ("custom_actions",),
    OptionsSource.collection: ("collection_id",),
    OptionsSource.collection_index: ("collection_index_ids",),
    # Forward-compat: no current node field uses these as an options_source (voice is signalled by
    # the voice_widget widget). A voice reference enters the projection via synthetic_voice_id — its
    # provider is read off the voice's FK, so there is no separate provider param field.
    OptionsSource.voice_provider_id: ("synthetic_voice_id",),
    OptionsSource.synthetic_voice_id: ("synthetic_voice_id",),
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

# ``Widgets`` that carry a resource reference -> the resource param field(s) they populate.
WIDGET_RESOURCES: dict[Widgets, tuple[str, ...]] = {
    Widgets.llm_provider_model: ("llm_provider_id", "llm_provider_model_id"),
    Widgets.voice_widget: ("synthetic_voice_id",),
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
        "(and add its param field(s) to RESOURCE_PARAM_FIELDS in inspect/nodes.py for resource ones)."
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


def test_every_resource_signal_maps_to_a_known_param_field():
    """Layer 2: every resource-bearing signal's backing param field(s) are registered in
    ``RESOURCE_PARAM_FIELDS``, so a newly added resource field can't silently land in ``params``."""
    referenced = {
        field for fields in (*OPTIONS_SOURCE_RESOURCES.values(), *WIDGET_RESOURCES.values()) for field in fields
    }
    missing = referenced - RESOURCE_PARAM_FIELDS.keys()
    assert not missing, (
        f"Resource signal param field(s) with no RESOURCE_PARAM_FIELDS entry: {sorted(missing)}. "
        "Add each to RESOURCE_PARAM_FIELDS in apps/api/v2/inspect/nodes.py."
    )
