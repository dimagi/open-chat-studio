"""Batched writes of node content for ``Pipeline.update_nodes_from_data``.

Kept out of ``models.py`` so the reconcile stays readable: the content rows are loaded in one
locked query and written with one ``bulk_update`` rather than an ``update_or_create`` round trip
per node, and the resource mirror is only re-derived for nodes whose type or params changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from apps.pipelines.flow import FlowNode, node_position_fields

if TYPE_CHECKING:
    from apps.pipelines.models import Node, Pipeline

_ALWAYS_UPDATED_FIELDS = ("type", "params", "label", "updated_at")


def content_nodes_from_data(node_data: dict[str, FlowNode | None], membership_only: set[str]) -> dict[str, FlowNode]:
    """The entries of ``node_data`` that carry content, keyed by flow_id."""
    content_nodes = {}
    for flow_id, node in node_data.items():
        if node is None or flow_id in membership_only:
            continue
        if node.id != flow_id:
            # The key is the flow_id the content is written under, so a mismatch would
            # silently store this node's content on a different row.
            raise ValueError(f"node_data key {flow_id!r} does not match node id {node.id!r}")
        content_nodes[flow_id] = node
    return content_nodes


def write_content_nodes(pipeline: Pipeline, content_nodes: dict[str, FlowNode]) -> None:
    """Create or update the ``Node`` rows for ``content_nodes`` and re-sync their resources."""
    from apps.pipelines.models import Node  # noqa: PLC0415 - circular: models imports this module

    if not content_nodes:
        return

    # One locked query (with the relations the resource sync reads) for every row being written.
    existing_by_flow_id = {
        existing.flow_id: existing
        for existing in Node.objects.select_for_update()
        .filter(pipeline=pipeline, flow_id__in=content_nodes)
        .prefetch_related("collection_indexes", "custom_action_operations")
    }

    now = timezone.now()
    update_fields = set(_ALWAYS_UPDATED_FIELDS)
    to_update = []
    needs_resource_sync = []
    for flow_id, node in content_nodes.items():
        existing = existing_by_flow_id.get(flow_id)
        if existing is None:
            _create_node(pipeline, node)
            continue
        if _apply_content(existing, node, update_fields, now):
            needs_resource_sync.append((existing, node.data.params))
        to_update.append(existing)

    if to_update:
        # Position columns are only in update_fields when some node supplied a position; nodes
        # that didn't keep their loaded values, so writing them back is a no-op.
        Node.objects.bulk_update(to_update, sorted(update_fields))

    # The FK/M2M/custom-action relations only depend on type and params, so a move or relabel
    # (the common autosave case) skips it entirely.
    for node_to_sync, editor_params in needs_resource_sync:
        _, resource_params = Node.split_resource_params(editor_params)
        node_to_sync.update_from_params(resource_params, team=pipeline.team)


def _create_node(pipeline: Pipeline, node: FlowNode) -> Node:
    """Create one node with ordinary params stored and resource values applied to relations."""
    from apps.pipelines.models import Node  # noqa: PLC0415 - circular: models imports this module

    content = node.data
    ordinary_params, resource_params = Node.split_resource_params(content.params)
    ordinary_params = content.node_type.params_to_store(ordinary_params)
    created = Node.objects.create(
        pipeline=pipeline,
        flow_id=node.id,
        type=content.type,
        params=ordinary_params,
        label=content.label,
        **node_position_fields(node.position),
    )
    created.update_from_params(resource_params, team=pipeline.team)
    return created


def _apply_content(existing: Node, node: FlowNode, update_fields: set[str], now) -> bool:
    """Copy ``node``'s content onto ``existing`` in memory; True when its resources may have changed."""
    from apps.pipelines.models import Node  # noqa: PLC0415 - circular: models imports this module

    content = node.data
    ordinary_params, resource_params = Node.split_resource_params(content.params)
    ordinary_params = content.node_type.params_to_store(ordinary_params)
    existing_resources = existing.resource_params()
    resources_changed = (
        existing.type != content.type
        or existing.params != ordinary_params
        or any(existing_resources.get(name) != value for name, value in resource_params.items())
    )
    existing.type = content.type
    existing.params = ordinary_params
    existing.label = content.label
    for field_name, value in node_position_fields(node.position).items():
        setattr(existing, field_name, value)
        update_fields.add(field_name)
    existing.updated_at = now
    return resources_changed
