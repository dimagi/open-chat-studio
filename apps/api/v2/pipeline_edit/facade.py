"""The one locked read-modify-write every pipeline façade endpoint runs (#4140, #4141, W2/W7).

Every façade edit is the same three steps around a different diff: lock the ``Pipeline`` row, hand
the UI builder's own patch engine a one-item ``PipelineDiffPayload``, then persist the way the UI
builder's save does. Reusing ``apply_pipeline_patch`` keeps the API off a second persistence path,
and inherits its rule that removing a node removes that node's edges with it.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction
from rest_framework.exceptions import NotFound
from rest_framework.generics import get_object_or_404

from apps.api.v2.lookups import get_working_chatbot
from apps.experiments.models import Experiment
from apps.oauth.permissions import enforce_application_chatbot_write
from apps.pipelines.flow import EdgeDiff, FlowEdge, NodeDiff, PipelineDiffPayload
from apps.pipelines.models import NODE_RESOURCE_PREFETCHES, Pipeline
from apps.pipelines.patching import apply_pipeline_patch

#: ``PipelineDiffPayload`` requires this, but ``apply_pipeline_patch`` never reads it: it is the UI
#: builder's optimistic-concurrency token, and the façade holds a row lock instead (W7).
UNUSED_BASE_REVISION = 0


@dataclass
class PipelineEdit:
    """One façade edit: the diff to apply, and which node or edge the response should describe.

    Both are optional and at most one is ever set: a delete names neither, and the response then
    reports the pipeline's state alone.
    """

    diff: PipelineDiffPayload
    node_id: str | None = None
    edge: FlowEdge | None = None

    def __post_init__(self) -> None:
        # Checked rather than merely documented: each view's `_write_response` reads only its own
        # field, so a plan that set both would answer with one resource's envelope and drop the
        # other, with nothing anywhere saying so.
        if self.node_id is not None and self.edge is not None:
            raise ValueError("A pipeline edit describes a node or an edge, never both.")


def graph_diff(nodes: NodeDiff | None = None, edges: EdgeDiff | None = None) -> PipelineDiffPayload:
    """One graph change -- to nodes, to edges, or to both -- in the shape the patch engine takes."""
    return PipelineDiffPayload(base_revision=UNUSED_BASE_REVISION, nodes=nodes or NodeDiff(), edges=edges or EdgeDiff())


def edit_pipeline(
    request,
    public_id: str,
    plan: Callable[[dict], PipelineEdit],
    respond: Callable[[Pipeline, PipelineEdit], dict],
) -> dict:
    """Apply one façade edit to the chatbot's working pipeline, under a row lock.

    ``plan`` is handed the current graph (``Pipeline.flow_data`` — nodes rebuilt from their rows,
    since ``Pipeline.data`` no longer lists them, ADR-0049) and returns the edit to make. It runs
    inside the lock, so what it reads is what gets written.

    ``respond`` runs inside the lock too, so that a body that cannot be built takes the write down
    with it: building it after the commit is how a node the server could not parse used to persist
    and then 500 every later read of the pipeline.

    The chatbot lookup and the permission check sit outside the transaction: neither writes, and a
    404 or a 403 has no reason to open one. Nothing is lost by moving them out -- the lock is on the
    ``Pipeline`` row, and ``_locked_pipeline`` restates the team boundary under it.
    """
    chatbot = get_working_chatbot(request.team, public_id)
    enforce_application_chatbot_write(request, chatbot)
    with transaction.atomic():
        pipeline = _locked_pipeline(chatbot)
        flow = pipeline.flow_data
        edit = plan(flow)
        _persist(pipeline, flow, edit.diff)
        return respond(pipeline, edit)


def _locked_pipeline(chatbot: Experiment) -> Pipeline:
    """The chatbot's pipeline, locked for the rest of the transaction.

    Team-scoped as well as addressed by pk: tenancy holds through the chatbot alone, but this is a
    write path and the boundary is cheap to restate. Prefetched because ``flow_data`` rebuilds every
    node from its row and reads the ``collection_indexes`` M2M per node.
    """
    if chatbot.pipeline_id is None:
        # Every chatbot the UI or POST /chatbots/ creates is pipeline-backed, but nothing in the
        # schema enforces it — so an older row without one is "no pipeline to edit", not a 500.
        raise NotFound("This chatbot has no pipeline.")
    # get_object_or_404 rather than .get(): the default manager hides archived rows, so an archived
    # pipeline is a DoesNotExist here and a 404 is the answer, not a 500.
    return get_object_or_404(
        Pipeline.objects.select_for_update().prefetch_related(*NODE_RESOURCE_PREFETCHES),
        pk=chatbot.pipeline_id,
        team_id=chatbot.team_id,
    )


def _persist(pipeline: Pipeline, flow: dict, diff: PipelineDiffPayload) -> None:
    """Merge ``diff`` into the graph and save it, exactly as ``_handle_pipeline_patch`` does.

    ``edit_revision`` is bumped for the UI builder's benefit: its own PATCH refuses a save whose
    ``base_revision`` has moved on, so leaving the revision alone would let an open UI builder
    session overwrite this edit without ever seeing a conflict.
    """
    edge_data, node_data = apply_pipeline_patch(flow, diff)
    pipeline.data = edge_data.model_dump()
    pipeline.edit_revision += 1
    pipeline.save(update_fields=["data", "edit_revision"])
    pipeline.update_nodes_from_data(node_data)
    # The rows were written behind the back of the prefetched node_set the graph above was built
    # from, so drop it before anything reads the pipeline back.
    pipeline.clear_node_caches()
