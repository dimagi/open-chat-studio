"""Completeness guard (ADR-0025, design D7).

Every ``OptionsSource`` and every ``Widgets`` value must be classified as either "embeds a
resource" or "explicitly not a resource". A new, unclassified signal fails this test rather than
silently omitting a resource reference from the inspect payload — the exact failure mode #3458
exists to prevent.
"""

from apps.api.v2.inspect.node_walker import (
    OPTIONS_SOURCE_NON_RESOURCES,
    OPTIONS_SOURCE_RESOURCES,
    WIDGET_NON_RESOURCES,
    WIDGET_RESOURCES,
)
from apps.pipelines.nodes.base import OptionsSource, Widgets


def test_every_options_source_is_classified():
    classified = set(OPTIONS_SOURCE_RESOURCES) | OPTIONS_SOURCE_NON_RESOURCES
    unclassified = set(OptionsSource) - classified
    assert not unclassified, (
        f"Unclassified OptionsSource value(s): {sorted(str(s) for s in unclassified)}. "
        "Register each in node_walker.OPTIONS_SOURCE_RESOURCES or OPTIONS_SOURCE_NON_RESOURCES."
    )


def test_options_source_classification_is_disjoint():
    overlap = set(OPTIONS_SOURCE_RESOURCES) & OPTIONS_SOURCE_NON_RESOURCES
    assert not overlap, f"OptionsSource value(s) classified as both resource and non-resource: {overlap}"


def test_every_widget_is_classified():
    classified = WIDGET_RESOURCES | WIDGET_NON_RESOURCES
    unclassified = set(Widgets) - classified
    assert not unclassified, (
        f"Unclassified Widget value(s): {sorted(str(w) for w in unclassified)}. "
        "Register each in node_walker.WIDGET_RESOURCES or WIDGET_NON_RESOURCES."
    )


def test_widget_classification_is_disjoint():
    overlap = WIDGET_RESOURCES & WIDGET_NON_RESOURCES
    assert not overlap, f"Widget value(s) classified as both resource and non-resource: {overlap}"
