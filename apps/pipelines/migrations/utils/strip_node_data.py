"""Strip embedded node content from ``Pipeline.data``, leaving layout only (ADR-0046).

Node content (type, label, params) is owned by the ``Node`` rows; the copy that older
rows embed under ``data.nodes[*].data`` is redundant. Idempotent and safe to rerun:
already-stripped rows produce no write.

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
