"""The version specs of the pipeline models.

A pipeline's snapshot carries its edges and its nodes; a node's snapshot carries its params, with
the records some params name pinned to versions, and the custom action operations the node owns.
"""

from typing import Any

from apps.experiments.versioning import (
    Archiving,
    Member,
    Operation,
    OwnedSet,
    ParamReference,
    Strategy,
    VersionSpec,
)
from apps.pipelines.flow import FlowWithoutNodes
from apps.pipelines.helper import duplicate_pipeline_with_new_ids


class FlowIds(Member):
    """How `Pipeline.data`, the edges, travels: a copy gets fresh node ids written onto them and provides
    the mapping to the nodes; a revert takes the version's edges and advances the edit revision."""

    def publish(self, working, version, context: dict[str, Any]) -> None:
        self._provide(context, {})

    def copy(self, working, copy, context: dict[str, Any]) -> None:
        node_types = dict(working.node_set.values_list("flow_id", "type"))
        copy.data, mapping = duplicate_pipeline_with_new_ids(working.data, node_types)
        self._provide(context, mapping)

    def revert(self, working, version) -> None:
        working.data = FlowWithoutNodes(**(version.data or {"edges": []})).model_dump()
        working.edit_revision += 1


def carry_nodes(working, other, operation: Operation, context: dict[str, Any]) -> None:
    """Bridge for `Pipeline.node_set`: publish and copy traverse each node's spec under the new pipeline,
    a copy with the fresh flow id `FlowIds` provided; revert rebuilds the working nodes from the
    version's, with the records their params name mapped back to working ids."""
    if operation is Operation.REVERT:
        node_data = {}
        for version_node in other.node_set.prefetch_related("collection_indexes", "custom_action_operations"):
            flow_node = version_node.to_flow_node()
            for member in version_node.node_type.versioned_param_specs:
                member.map_back(version_node, flow_node.data.params)
            node_data[version_node.flow_id] = flow_node
        working.update_nodes_from_data(node_data)
        return
    spec = NodeVersionSpec()
    for node in working.node_set.all():
        if operation is Operation.PUBLISH:
            spec.publish(node, pipeline=other)
        else:
            spec.copy(node, pipeline=other, new_flow_id=context["flow_ids"].get(node.flow_id))


def carry_custom_action_operations(working, other, operation: Operation, context: dict[str, Any]) -> None:
    """Bridge for `Node.custom_action_operations` until `CustomActionOperation` has a spec: each row the
    working node owns is cloned under the new node."""
    if operation is Operation.REVERT or not working.node_type.declares("custom_actions"):
        return
    working._copy_custom_action_operations_to_new_version(new_node=other, is_copy=operation is Operation.COPY)


class PipelineVersionSpec(VersionSpec):
    """How a pipeline travels: its edges through `FlowIds`, its nodes under the pipeline being made."""

    class Meta:
        model = "pipelines.Pipeline"
        identity = frozenset(
            {
                "id",
                "team",
                "created_at",
                "updated_at",
                "working_version",
                "version_number",
                "is_archived",
                "edit_revision",
            }
        )

    data = FlowIds(provides="flow_ids")
    node_set = OwnedSet(via=carry_nodes, reads=("flow_ids",))

    def on_revert(self, working, version) -> None:
        working.clear_node_caches()


class NodeVersionSpec(VersionSpec):
    """How a node travels: its params as they are, but for the records they name, under the pipeline
    being made; `Node.archive` and `Node.unarchive` read the same param references."""

    class Meta:
        model = "pipelines.Node"
        identity = frozenset(
            {"id", "created_at", "updated_at", "working_version", "is_archived", "archived_with_pipeline", "pipeline"}
        )

    source_material = ParamReference(
        "source_material_id",
        "experiments.SourceMaterial",
        Strategy.REUSE_UNCHANGED,
        archiving=Archiving.ARCHIVE,
        display_name="source_material",
    )
    # ADR-0031: collections, media and index alike, are live shared resources. Only a frozen per-bot
    # version (legacy data) is ever archived, never the live working collection.
    collection = ParamReference(
        "collection_id",
        "documents.Collection",
        Strategy.LIVE_REFERENCE,
        archiving=Archiving.ARCHIVE_VERSIONS_ONLY,
        display_name="media",
    )
    collection_indexes = ParamReference(
        "collection_index_ids",
        "documents.Collection",
        Strategy.LIVE_REFERENCE,
        archiving=Archiving.ARCHIVE_VERSIONS_ONLY,
        display_name="Collection Indexes",
        many=True,
    )
    custom_action_operations = OwnedSet(via=carry_custom_action_operations)

    def members_for(self, row) -> tuple[Member, ...]:
        """A node's param references are only the ones its type declares: an id a removed or changed type
        left in `params` travels verbatim, as it did before the spec."""
        declared = row.node_type.versioned_param_specs
        return tuple(m for m in self.members if not isinstance(m, ParamReference) or m in declared)

    def on_clone(self, working, clone, *, pipeline=None, new_flow_id: str | None = None, **options) -> None:
        if pipeline is not None:
            clone.pipeline = pipeline
        if new_flow_id:
            if not clone.node_type.is_server_managed and clone.name == clone.flow_id:
                clone.params["name"] = new_flow_id
            clone.flow_id = new_flow_id

    def on_publish(self, working, version, **options) -> None:
        version._sync_resource_fk_fields()

    def on_copy(self, working, copy, **options) -> None:
        copy._sync_resource_fk_fields()
