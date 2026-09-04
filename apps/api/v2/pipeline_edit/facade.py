"""The locked read-modify-write every pipeline façade endpoint runs (#4140, #4141).

Every edit is the same three steps around a different diff: lock the ``Pipeline`` row, hand the diff
to the UI builder's own patch engine, then persist the way its save does. Reusing
``apply_pipeline_patch`` keeps the API off a second persistence path, and inherits its rule that
removing a node removes that node's edges with it.
"""

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from django.db import transaction
from rest_framework.exceptions import NotFound
from rest_framework.generics import get_object_or_404

from apps.api.v2.lookups import get_working_chatbot
from apps.experiments.models import Experiment
from apps.oauth.permissions import enforce_application_chatbot_write
from apps.pipelines.flow import EdgeDiff, Flow, NodeDiff, PipelineDiffPayload
from apps.pipelines.models import NODE_RESOURCE_PREFETCHES, Pipeline
from apps.pipelines.patching import apply_pipeline_patch

#: ``PipelineDiffPayload`` requires this, but ``apply_pipeline_patch`` never reads it: it is the UI
#: builder's optimistic-concurrency token, and the façade holds a row lock instead (W7).
UNUSED_BASE_REVISION = 0


@dataclass
class PipelineEdit:
    """One façade edit: the diff to apply, and the ids of what the response should describe.

    Ids rather than one field per resource kind -- each view already knows which kind it serves. A
    delete names none, and the response then reports the pipeline's state alone.
    """

    diff: PipelineDiffPayload
    written_ids: list[str] = field(default_factory=list)


def graph_diff(nodes: NodeDiff | None = None, edges: EdgeDiff | None = None) -> PipelineDiffPayload:
    """One graph change -- to nodes, to edges, or to both -- in the shape the patch engine takes."""
    return PipelineDiffPayload(base_revision=UNUSED_BASE_REVISION, nodes=nodes or NodeDiff(), edges=edges or EdgeDiff())


def edit_pipeline(
    request,
    public_id: str,
    plan: Callable[[Flow], PipelineEdit],
    respond: Callable[[Pipeline, list[str]], dict],
) -> dict:
    """Apply one façade edit to the chatbot's working pipeline, under a row lock.

    ``plan`` is handed the current graph and returns the edit to make. ``respond`` builds the
    response body. Both run inside the lock: what ``plan`` reads is what gets written, and a body
    that cannot be built takes the write down with it, rather than persisting a node the server
    cannot parse and then 500ing on every later read of the pipeline.

    The chatbot lookup and the permission check sit outside the transaction: neither writes, and a
    404 or a 403 has no reason to open one. The lock is on the ``Pipeline`` row, and
    ``_locked_pipeline`` restates the team boundary under it.
    """
    chatbot = get_working_chatbot(request.team, public_id)
    enforce_application_chatbot_write(request, chatbot)
    with transaction.atomic():
        pipeline = _locked_pipeline(chatbot)
        # Nodes are rebuilt from their rows, since ``Pipeline.data`` no longer lists them (ADR-0049).
        # The patch engine takes the raw graph, so the planners get a parsed view of the same read.
        graph = pipeline.flow_data
        edit = plan(Flow(**graph))
        _persist(pipeline, graph, edit.diff)
        return respond(pipeline, edit.written_ids)


def _locked_pipeline(chatbot: Experiment) -> Pipeline:
    """The chatbot's pipeline, locked for the rest of the transaction.

    Team-scoped as well as addressed by pk: tenancy holds through the chatbot alone, but this is a
    write path and the boundary is cheap to restate. Prefetched because ``flow_data`` rebuilds every
    node from its row and reads the ``collection_indexes`` M2M per node.
    """
    if chatbot.pipeline_id is None:
        # Every chatbot the UI or the `chatbot_create` endpoint creates is pipeline-backed, but
        # nothing in the schema enforces it — so an older row without one is "no pipeline to edit", not a 500.
        raise NotFound("This chatbot has no pipeline.")
    # get_object_or_404 rather than .get(): the default manager hides archived rows, so an archived
    # pipeline is a DoesNotExist here and a 404 is the answer, not a 500.
    return get_object_or_404(
        Pipeline.objects.select_for_update().prefetch_related(*NODE_RESOURCE_PREFETCHES),
        pk=chatbot.pipeline_id,
        team_id=chatbot.team_id,
    )


def _persist(pipeline: Pipeline, graph: dict, diff: PipelineDiffPayload) -> None:
    """Merge ``diff`` into the graph and save it, exactly as ``_handle_pipeline_patch`` does.

    ``edit_revision`` is bumped for the UI builder's benefit: its own PATCH refuses a save whose
    ``base_revision`` has moved on, so leaving the revision alone would let an open UI builder
    session overwrite this edit without ever seeing a conflict.
    """
    edge_data, node_data = apply_pipeline_patch(graph, diff)
    pipeline.data = edge_data.model_dump()
    pipeline.edit_revision += 1
    pipeline.save(update_fields=["data", "edit_revision"])
    if _changes_node_rows(diff.nodes):
        pipeline.update_nodes_from_data(node_data)
        # The rows were written behind the back of the prefetched node_set the graph above was built
        # from, so drop it before anything reads the pipeline back.
        pipeline.clear_node_caches()
    else:
        # An edge-only diff cannot touch a node row: ``_collect_node_data`` maps every node to
        # membership-only, and ``update_nodes_from_data`` writes nothing for those. Skipping it keeps
        # its savepoint and membership SELECT out of the lock, and leaves the prefetched ``node_set``
        # intact for the build state that follows, where ``clear_node_caches`` would have thrown away
        # the prefetch the locked read just paid for. Dropping ``flow_data`` is insurance: it is
        # stale from here, and nothing reads it.
        with contextlib.suppress(AttributeError):  # nothing cached if it was never read
            del pipeline.flow_data


def _changes_node_rows(nodes: NodeDiff) -> bool:
    """Whether this diff can touch a ``Node`` row at all.

    Only the edge endpoints ever produce a diff that cannot. Compared against an empty diff rather
    than testing the three lists, so a fourth kind of node change added to ``NodeDiff`` is covered
    without editing this.
    """
    return nodes != NodeDiff()
