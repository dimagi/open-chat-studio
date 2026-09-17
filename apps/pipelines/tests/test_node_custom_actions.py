import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.custom_actions.models import CustomActionOperation
from apps.pipelines.tests.utils import content_flow_node
from apps.utils.factories.custom_actions import CustomActionFactory, CustomActionOperationFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
class TestNodeCustomActionsSync:
    """update_from_params() keeps a node's CustomActionOperation rows in sync with its params."""

    def test_custom_action_operations_cleared_when_node_changes_away_from_llm_type(self):
        """#1452: a type change resets params, dropping ``custom_actions``. The sync branch that
        writes those rows only fires for the *current* type, so a plain reset leaves the old
        LLMResponseWithPrompt-only rows orphaned on a node that is no longer that type."""
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"name": "llm", "custom_actions": []},
        )
        action = CustomActionFactory.create(allowed_operations=["weather_get"])
        CustomActionOperationFactory.create(node=node, custom_action=action, operation_id="weather_get")
        assert node.custom_action_operations.exists()

        node.type = "RenderTemplate"
        node.params = {"name": "llm"}
        node.save(update_fields=["type", "params"])
        node.update_from_params()

        assert not node.custom_action_operations.exists()

    def test_reconciling_many_nodes_reads_custom_action_rows_once_not_per_node(self):
        """update_nodes_from_data's whole point is not paying an update_from_params() query per
        node for something it can answer for the whole batch in one -- verified here for the
        custom_actions existence check specifically, since none of these nodes declare the field
        at all and previously still cost one query each."""
        pipeline = PipelineFactory.create()
        nodes = [NodeFactory.create(pipeline=pipeline, type="RenderTemplate", flow_id=f"tmpl-{i}") for i in range(5)]
        # Membership-only for whatever PipelineFactory already seeded (Start/End), so this
        # reconcile doesn't also delete them -- that's a real, separate query path (cascade
        # delete), not the one this test is isolating.
        node_data = {flow_id: None for flow_id in pipeline.node_ids}
        node_data.update(
            {
                node.flow_id: content_flow_node(node.flow_id, "RenderTemplate", params={"name": node.flow_id})
                for node in nodes
            }
        )

        with CaptureQueriesContext(connection) as captured:
            pipeline.update_nodes_from_data(node_data)

        reads = [q for q in captured.captured_queries if CustomActionOperation._meta.db_table in q["sql"]]
        assert len(reads) == 1, f"{len(reads)} reads of {CustomActionOperation._meta.db_table} for 5 nodes"

    def test_reconciling_a_node_changed_away_from_llm_still_clears_orphaned_rows(self):
        """The batched existence check update_nodes_from_data now passes in must still catch the
        same orphaned-rows case the per-node check used to (see the sibling test above), just
        computed once for the whole reconcile instead of once per node."""
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            pipeline=pipeline, type="LLMResponseWithPrompt", flow_id="llm-1", params={"name": "llm-1"}
        )
        action = CustomActionFactory.create(allowed_operations=["weather_get"])
        CustomActionOperationFactory.create(node=node, custom_action=action, operation_id="weather_get")
        assert node.custom_action_operations.exists()

        node_data = {"llm-1": content_flow_node("llm-1", "RenderTemplate", params={"name": "llm-1"})}
        pipeline.update_nodes_from_data(node_data)

        node.refresh_from_db()
        assert not node.custom_action_operations.exists()

    def test_malformed_custom_action_entry_on_a_non_llm_node_does_not_crash(self):
        """A node whose type doesn't declare ``custom_actions`` (e.g. left over from a type
        change, or a hand-edited export) can carry a garbage value under that key -- it must be
        ignored, not crash the save on the unpack below."""
        node = NodeFactory.create(type="RenderTemplate", params={"name": "tmpl", "custom_actions": ["not-a-valid-id"]})

        node.update_from_params()

        assert not node.custom_action_operations.exists()


@pytest.mark.django_db()
class TestFlowNodeReadsCustomActions:
    """to_flow_node() and version_details serve custom_actions from the operation rows."""

    def test_custom_actions_come_from_the_operation_rows(self):
        """Params carries no entries, so the served list can only have come off the
        ``CustomActionOperation`` rows -- sorted, since they have no ordering of their own here."""
        node = NodeFactory.create(type="LLMResponseWithPrompt", params={"name": "llm"})
        action = CustomActionFactory.create(allowed_operations=["weather_get", "pollen_get"])
        for operation_id in ("weather_get", "pollen_get"):
            CustomActionOperationFactory.create(node=node, custom_action=action, operation_id=operation_id)

        assert node.to_flow_node().data.params["custom_actions"] == sorted(
            [f"{action.id}:weather_get", f"{action.id}:pollen_get"]
        )

    def test_version_details_include_custom_actions_for_a_node_that_declares_them(self):
        """The version-details display gate must agree with has_parameter, the same check
        update_from_params uses to decide whether to write rows -- otherwise a node can have real
        rows that the version diff silently refuses to show."""
        action = CustomActionFactory.create(allowed_operations=["weather_get"])
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"name": "llm", "custom_actions": [f"{action.id}:weather_get"]},
        )

        assert node.version_details.get_field("custom_actions") is not None

    def test_version_details_omit_custom_actions_for_a_node_of_another_type(self):
        node = NodeFactory.create(type="RenderTemplate", params={"name": "tmpl"})

        assert node.version_details.get_field("custom_actions") is None

    def test_deleted_custom_action_reads_as_empty(self):
        """Deleting a CustomAction cascades the operation row away while the entry lingers in params.
        The params copy must not be served -- the same correction the scalar FKs get."""
        action = CustomActionFactory.create()
        node = NodeFactory.create(
            type="LLMResponseWithPrompt",
            params={"name": "llm", "custom_actions": [f"{action.id}:weather_get"]},
        )
        node.update_from_params()
        assert node.custom_action_operations.exists()

        action.delete()
        node.refresh_from_db()
        assert node.params["custom_actions"] != []

        assert node.to_flow_node().data.params["custom_actions"] == []
