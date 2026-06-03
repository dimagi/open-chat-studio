"""Completeness guard (ADR-0025, design D7).

Every ``OptionsSource`` and every ``Widgets`` value must be classified as either "embeds a
resource" or "explicitly not a resource". A new, unclassified signal fails this test rather than
silently omitting a resource reference from the inspect payload — the exact failure mode #3458
exists to prevent.

The resource side of the classification lives in ``node_walker`` (it drives the walker at
runtime); the non-resource sets below exist only for this guard, so the "everything else" bucket
is an explicit, reviewed decision rather than a silent default.
"""

from apps.api.v2.inspect.node_walker import OPTIONS_SOURCE_RESOURCES
from apps.pipelines.nodes.base import OptionsSource, Widgets

# ``OptionsSource`` values that are explicitly NOT resource references (tool enums, autocomplete
# variable hints, jinja editors) — their fields stay verbatim in ``params``.
OPTIONS_SOURCE_NON_RESOURCES: set[OptionsSource] = {
    OptionsSource.agent_tools,
    OptionsSource.built_in_tools,
    OptionsSource.built_in_tools_config,
    OptionsSource.mcp_tools,
    OptionsSource.jinja_node,
    OptionsSource.text_editor_autocomplete_vars_llm_node,
    OptionsSource.text_editor_autocomplete_vars_router_node,
}

# Widget signals that carry a resource reference when there is no options_source. These mirror the
# explicit widget handling in ``node_walker.walk_node`` (``llm_provider_model`` marks the LLM
# provider/model pair; ``voice_widget`` marks the synthetic-voice field).
WIDGET_RESOURCES: set[Widgets] = {Widgets.llm_provider_model, Widgets.voice_widget}

# Every other widget is presentational and not, on its own, a resource signal. Enumerated
# explicitly so a newly added widget trips the completeness guard.
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
        "Register each in node_walker.OPTIONS_SOURCE_RESOURCES or OPTIONS_SOURCE_NON_RESOURCES "
        "in this file."
    )


def test_options_source_classification_is_disjoint():
    overlap = set(OPTIONS_SOURCE_RESOURCES) & OPTIONS_SOURCE_NON_RESOURCES
    assert not overlap, f"OptionsSource value(s) classified as both resource and non-resource: {overlap}"


def test_every_widget_is_classified():
    classified = WIDGET_RESOURCES | WIDGET_NON_RESOURCES
    unclassified = set(Widgets) - classified
    assert not unclassified, (
        f"Unclassified Widget value(s): {sorted(str(w) for w in unclassified)}. "
        "Register each in WIDGET_RESOURCES or WIDGET_NON_RESOURCES in this file "
        "(and handle resource widgets in node_walker.walk_node)."
    )


def test_widget_classification_is_disjoint():
    overlap = WIDGET_RESOURCES & WIDGET_NON_RESOURCES
    assert not overlap, f"Widget value(s) classified as both resource and non-resource: {overlap}"
