"""Drop the ``nodes`` key from ``Pipeline.data`` and backfill row positions (ADR-0049).

Node content (type, label, params) and layout (position) are owned by the ``Node`` rows;
``Pipeline.data`` keeps only edges. This helper first mirrors each node's position from the
blob onto the row's position columns — the rows are the authoritative layout source that
reads now use — then removes the ``nodes`` key entirely. The backfill is
the load-bearing half: without it a row reads back at the origin and the next save drops the
blob holding the only copy of its layout. Running it at deploy also heals any writer that
bypassed the shadow-write added with the columns (e.g. revert restoring old layout data).
Idempotent and safe to rerun: rows already positioned produce no write, and data already
without a ``nodes`` key is skipped.

The strip is a targeted key removal, so a ``viewport`` left over from an older blob stays put
here. The flow models no longer carry that key (ADR-0049), so it falls away on the pipeline's
next save instead — nothing reads it in the meantime.

Migration ``pipelines.0030_strip_node_data`` runs ``strip_node_data_from_pipelines``; its
reverse runs ``rebuild_node_data_in_pipelines``. Both are called with historical models,
so they stick to ``_base_manager`` and plain column access.
"""

import logging

from apps.pipelines.flow import react_flow_node_type

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def strip_node_data_from_pipelines(Pipeline, Node):
    queryset = Pipeline._base_manager.exclude(data__isnull=True)

    pending = []
    for pipeline in queryset.iterator(chunk_size=BATCH_SIZE):
        rows = list(Node._base_manager.filter(pipeline_id=pipeline.id).order_by("-is_archived"))

        _backfill_positions(pipeline, Node, node_rows=rows)
        stripped_data = _strip_nodes(pipeline, rows)
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


def _strip_nodes(pipeline, node_rows):
    """The pipeline's data with the ``nodes`` key removed, or None when nothing to strip.

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


def rebuild_node_data_in_pipelines(Pipeline, Node):
    """Reverse of the strip: rebuild the full ``nodes`` list from the Node rows.

    ``Pipeline.data`` no longer stores nodes, so a code rollback to a version that reads
    them needs the react-flow nodes — id, react-flow type, position and embedded content
    blob — reconstructed from the rows. Pipelines that still carry a ``nodes`` key are left
    untouched (idempotent); pipelines with no backing rows are skipped.
    """
    queryset = Pipeline._base_manager.exclude(data__isnull=True)

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
