"""Strip embedded node content from ``Pipeline.data`` and backfill row positions (ADR-0046).

Node content (type, label, params) is owned by the ``Node`` rows; the copy that older
rows embed under ``data.nodes[*].data`` is redundant. Each node's position is also
copied onto the row's position columns — the blob stays authoritative for layout until
a follow-up PR switches reads to the columns, so this backfill mirrors, never moves.
Idempotent and safe to rerun: already-synced rows produce no write.

Run via the ``strip_node_data`` management command; a data migration in a follow-up PR
will reuse these helpers with historical models.
"""

import logging

logger = logging.getLogger(__name__)

LAYOUT_NODE_KEYS = ("id", "type", "position")
BATCH_SIZE = 500


def strip_node_data_from_pipelines(Pipeline, Node):
    pending = []
    for pipeline in Pipeline._base_manager.exclude(data__isnull=True).iterator(chunk_size=BATCH_SIZE):
        _backfill_positions(pipeline, Node)
        stripped_nodes = _strip_nodes(pipeline, Node)
        if stripped_nodes is None:
            continue
        pipeline.data = {**pipeline.data, "nodes": stripped_nodes}
        pending.append(pipeline)
        if len(pending) >= BATCH_SIZE:
            Pipeline._base_manager.bulk_update(pending, ["data"])
            pending.clear()

    if pending:
        Pipeline._base_manager.bulk_update(pending, ["data"])


def _backfill_positions(pipeline, Node):
    """Copy each blob node's position onto its row's position columns.

    The blob is authoritative, so a differing row value is overwritten. Blob nodes
    without a usable ``{"x", "y"}`` position or without a backing row are skipped.
    """
    nodes = (pipeline.data or {}).get("nodes") or []
    if not nodes:
        return
    rows = {}
    # "-is_archived" puts archived rows first so a non-archived row wins on flow_id collision
    for row in Node._base_manager.filter(pipeline_id=pipeline.id).order_by("-is_archived"):
        rows[row.flow_id] = row
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


def _strip_nodes(pipeline, Node):
    """The pipeline's node list with only layout keys kept, or None when nothing to strip.

    Also None when any blob has no backing Node row (drift, a known bad state): the blob
    is then the only copy of that node's content, so the pipeline is skipped and logged
    for manual healing rather than have its content destroyed. Archived rows count —
    they still hold the content.
    """
    nodes = (pipeline.data or {}).get("nodes") or []
    if not any(set(node) - set(LAYOUT_NODE_KEYS) for node in nodes):
        return None

    row_flow_ids = set(Node._base_manager.filter(pipeline_id=pipeline.id).values_list("flow_id", flat=True))
    orphaned = [node.get("id") for node in nodes if "data" in node and node.get("id") not in row_flow_ids]
    if orphaned:
        logger.warning(
            "strip_node_data: skipping pipeline %s (team %s): node blob(s) %s have no Node row",
            pipeline.id,
            pipeline.team_id,
            orphaned,
        )
        return None

    return [{key: node[key] for key in LAYOUT_NODE_KEYS if key in node} for node in nodes]


def rebuild_node_data_in_pipelines(Pipeline, Node):
    """Reverse of the strip: rebuild each node's embedded content blob from its Node row.

    Exists so the strip is genuinely reversible — pre-ADR-0046 code requires the
    blob (``FlowNode.data`` was a mandatory field), so a code rollback needs it restored.
    Nodes without a backing row are left untouched. Idempotent.
    """
    pending = []
    for pipeline in Pipeline._base_manager.exclude(data__isnull=True).iterator(chunk_size=BATCH_SIZE):
        nodes = (pipeline.data or {}).get("nodes") or []
        if not nodes:
            continue
        rows = {}
        # "-is_archived" puts archived rows first so a non-archived row wins on flow_id collision
        for row in Node._base_manager.filter(pipeline_id=pipeline.id).order_by("-is_archived"):
            rows[row.flow_id] = row
        changed = False
        new_nodes = []
        for node in nodes:
            row = rows.get(node.get("id"))
            blob = row and {"id": row.flow_id, "type": row.type, "label": row.label, "params": row.params}
            if row is None or node.get("data") == blob:
                new_nodes.append(node)
                continue
            new_nodes.append({**node, "data": blob})
            changed = True
        if not changed:
            continue
        pipeline.data = {**pipeline.data, "nodes": new_nodes}
        pending.append(pipeline)
        if len(pending) >= BATCH_SIZE:
            Pipeline._base_manager.bulk_update(pending, ["data"])
            pending.clear()

    if pending:
        Pipeline._base_manager.bulk_update(pending, ["data"])
