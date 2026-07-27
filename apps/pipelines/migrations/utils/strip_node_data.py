"""Drop the ``nodes`` key from ``Pipeline.data`` and backfill row positions (ADR-0047).

Node content (type, label, params) and layout (position) are owned by the ``Node`` rows;
``Pipeline.data`` keeps only edges (and viewport). This helper first mirrors each node's
position from the blob onto the row's position columns — the rows are the authoritative
layout source once reads switch over — then removes the ``nodes`` key entirely. Running it
at deploy also heals any writer that bypassed the phase-1 shadow-write (e.g. revert
restoring old layout data). Idempotent and safe to rerun: rows already positioned produce
no write, and data already without a ``nodes`` key is skipped.

Run via the ``strip_node_data`` management command; a data migration in a follow-up PR
will reuse these helpers with historical models.
"""

import logging

from apps.pipelines.flow import react_flow_node_type

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def strip_node_data_from_pipelines(Pipeline, Node, team=None, progress_callback=None):
    """`team`, if given, scopes the strip to that team's pipelines only.

    `progress_callback(processed, total)` is invoked every ``BATCH_SIZE`` pipelines
    and once more on the final pipeline, if given.
    """
    queryset = Pipeline._base_manager.exclude(data__isnull=True)
    if team is not None:
        queryset = queryset.filter(team=team)
    total = queryset.count()

    pending = []
    processed = 0
    for pipeline in queryset.iterator(chunk_size=BATCH_SIZE):
        rows = []
        for row in Node._base_manager.filter(pipeline_id=pipeline.id).order_by("-is_archived"):
            rows.append(row)

        _backfill_positions(pipeline, Node, node_rows=rows)
        stripped_data = _strip_nodes(pipeline, Node, rows)
        processed += 1
        if progress_callback and (processed % BATCH_SIZE == 0 or processed == total):
            progress_callback(processed, total)
        if stripped_data is None:
            continue
        pipeline.data = stripped_data
        pending.append(pipeline)
        if len(pending) >= BATCH_SIZE:
            Pipeline._base_manager.bulk_update(pending, ["data"])
            pending.clear()

    if pending:
        Pipeline._base_manager.bulk_update(pending, ["data"])


def _backfill_positions(pipeline, Node, node_rows):
    """Copy each blob node's position onto its row's position columns.

    The blob is authoritative, so a differing row value is overwritten. Blob nodes
    without a usable ``{"x", "y"}`` position or without a backing row are skipped.
    """
    nodes = (pipeline.data or {}).get("nodes") or []
    if not nodes:
        return
    rows = {row.flow_id: row for row in node_rows}
    rows_to_update = []
    for node in nodes:
        row = rows.get(node.get("id"))
        position = node.get("position")
        if row is None or not isinstance(position, dict):
            continue
        x, y = position.get("x"), position.get("y")
        if not isinstance(x, int | float) or not isinstance(y, int | float):
            continue
        if (row.position_x, row.position_y) == (x, y):
            continue
        row.position_x = x
        row.position_y = y
        rows_to_update.append(row)
    if rows_to_update:
        Node._base_manager.bulk_update(rows_to_update, ["position_x", "position_y"])


def _strip_nodes(pipeline, Node, node_rows):
    """The pipeline's node list with only layout keys kept, or None when nothing to strip.

    None when there is no ``nodes`` key (already stripped — idempotent), and also None when
    any blob has no backing Node row (drift, a known bad state): the blob is then the only
    copy of that node's content, so the pipeline is skipped and logged for manual healing
    rather than have its content destroyed. Archived rows count — they still hold the
    content.
    """
    data = pipeline.data or {}
    if "nodes" not in data:
        return None
    nodes = data.get("nodes") or []

    row_flow_ids = set([row.flow_id for row in node_rows])
    orphaned = [node.get("id") for node in nodes if "data" in node and node.get("id") not in row_flow_ids]
    if orphaned:
        logger.warning(
            "strip_node_data: skipping pipeline %s (team %s): node blob(s) %s have no Node row",
            pipeline.id,
            pipeline.team_id,
            orphaned,
        )
        return None

    return {key: value for key, value in data.items() if key != "nodes"}


def rebuild_node_data_in_pipelines(Pipeline, Node, team=None):
    """Reverse of the strip: rebuild each node's embedded content blob from its Node row.

    Exists so the strip is genuinely reversible — pre-ADR-0046 code requires the
    blob (``FlowNode.data`` was a mandatory field), so a code rollback needs it restored.
    Nodes without a backing row are left untouched. Idempotent.

    `team`, if given, scopes the rebuild to that team's pipelines only.
    """
    queryset = Pipeline._base_manager.exclude(data__isnull=True)
    if team is not None:
        queryset = queryset.filter(team=team)

    pending = []
    for pipeline in queryset.iterator(chunk_size=BATCH_SIZE):
        data = pipeline.data or {}
        if data.get("nodes"):
            continue
        rows = list(Node._base_manager.filter(pipeline_id=pipeline.id, is_archived=False))
        if not rows:
            continue
        nodes = []
        for row in rows:
            node = {
                "id": row.flow_id,
                "type": react_flow_node_type(row.type),
                "data": {"id": row.flow_id, "type": row.type, "label": row.label, "params": row.params},
            }
            # Read the columns directly (not the Node.position property) so a future data
            # migration can reuse this with historical models, which have no properties.
            if row.position_x is not None and row.position_y is not None:
                node["position"] = {"x": row.position_x, "y": row.position_y}
            nodes.append(node)
        pipeline.data = {**data, "nodes": nodes}
        pending.append(pipeline)
        if len(pending) >= BATCH_SIZE:
            Pipeline._base_manager.bulk_update(pending, ["data"])
            pending.clear()

    if pending:
        Pipeline._base_manager.bulk_update(pending, ["data"])
