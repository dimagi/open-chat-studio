import importlib

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.data_migrations.models import CustomMigration
from apps.data_migrations.utils.migrations import is_migration_applied
from apps.pipelines.management.commands.strip_node_data import Command as StripNodeDataCommand
from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)
from apps.pipelines.models import Node, Pipeline

MIGRATION_NAME = StripNodeDataCommand.migration_name
# Leading digit, so it cannot be imported with a plain import statement.
migration_0030 = importlib.import_module("apps.pipelines.migrations.0030_strip_node_data")


@pytest.fixture()
def unapplied_migration():
    """Clear the run-once marker that migration 0030 set when the test database was built.

    Without this the command short-circuits as already-applied and the command tests pass
    vacuously. The delete is rolled back with the test transaction.
    """
    CustomMigration.objects.filter(name=MIGRATION_NAME).delete()


def _old_format_data():
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"id": "start-1", "type": "StartNode", "params": {"name": "start"}},
            },
            {
                "id": "end-1",
                "type": "endNode",
                "position": {"x": 100, "y": 0},
                "data": {"id": "end-1", "type": "EndNode", "params": {"name": "end"}},
            },
        ],
        "edges": [{"id": "e1", "source": "start-1", "target": "end-1"}],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _create_old_format_pipeline(team, with_rows=True):
    pipeline = Pipeline.objects.create(team=team, name="old", data=_old_format_data())
    if with_rows:
        for node in pipeline.data["nodes"]:
            Node.objects.create(
                pipeline=pipeline,
                flow_id=node["id"],
                type=node["data"]["type"],
                params=node["data"]["params"],
            )
    return pipeline


@pytest.mark.django_db()
class TestStripNodeData:
    def test_drops_nodes_key_and_preserves_edges(self, team):
        pipeline = _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert "nodes" not in pipeline.data
        assert pipeline.data["edges"] == _old_format_data()["edges"]
        assert pipeline.data["viewport"] == _old_format_data()["viewport"]
        # rows untouched
        assert pipeline.node_set.get(flow_id="start-1").params == {"name": "start"}

    def test_is_idempotent(self, team):
        pipeline = _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)
        pipeline.refresh_from_db()
        first_pass = pipeline.data

        strip_node_data_from_pipelines(Pipeline, Node)
        pipeline.refresh_from_db()
        assert pipeline.data == first_pass

    def test_skips_pipeline_whose_blob_has_no_matching_row(self, team, caplog):
        """A blob without a backing Node row is the only copy of that node's content —
        never destroy it; skip and log so it can be healed manually."""
        pipeline = _create_old_format_pipeline(team, with_rows=False)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == _old_format_data()
        assert any("skip" in record.message.lower() for record in caplog.records)

    def test_archived_rows_count_as_backing_rows(self, team):
        pipeline = _create_old_format_pipeline(team)
        pipeline.node_set.update(is_archived=True)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert "nodes" not in pipeline.data

    def test_drops_content_less_nodes_without_rows(self, team):
        """A layout-only node with no backing row (no content blob to lose) is not an
        orphan; its nodes key is dropped like any other."""
        pipeline = Pipeline.objects.create(team=team, name="ghost", data={"nodes": [{"id": "a"}], "edges": []})

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == {"edges": []}

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({}, id="empty-data"),
            pytest.param({"edges": []}, id="no-nodes-key"),
            pytest.param({"nodes": [{"data": {"type": "StartNode"}}], "edges": []}, id="blob-node-without-id"),
        ],
    )
    def test_tolerates_degenerate_data(self, team, data):
        pipeline = Pipeline.objects.create(team=team, name="degenerate", data=data)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == data

    def test_progress_callback_is_optional(self, team):
        _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)


@pytest.mark.django_db()
class TestBackfillPositions:
    """The strip also mirrors each blob node's position onto the row's position columns,
    so the follow-up PR can switch layout reads to the rows."""

    def test_copies_blob_positions_onto_rows(self, team):
        pipeline = _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="start-1").position == {"x": 0, "y": 0}
        assert pipeline.node_set.get(flow_id="end-1").position == {"x": 100, "y": 0}

    def test_backfills_already_stripped_pipelines(self, team):
        """Layout-only data has nothing to strip but its rows may still lack positions."""
        pipeline = _create_old_format_pipeline(team)
        pipeline.data = {
            **pipeline.data,
            "nodes": [{key: node[key] for key in ("id", "type", "position")} for node in pipeline.data["nodes"]],
        }
        pipeline.save(update_fields=["data"])

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="start-1").position == {"x": 0, "y": 0}

    def test_overwrites_differing_row_position(self, team):
        """The blob is authoritative for layout until reads switch to the columns."""
        pipeline = _create_old_format_pipeline(team)
        pipeline.node_set.filter(flow_id="start-1").update(position_x=999, position_y=999)

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="start-1").position == {"x": 0, "y": 0}

    def test_skips_unusable_positions(self, team):
        pipeline = _create_old_format_pipeline(team)
        data = pipeline.data
        data["nodes"][0]["position"] = {"x": "abc", "y": 2}
        data["nodes"][1].pop("position")
        pipeline.data = data
        pipeline.save(update_fields=["data"])

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="start-1").position is None
        assert pipeline.node_set.get(flow_id="end-1").position is None

    def test_non_archived_row_wins_flow_id_collision(self, team):
        pipeline = _create_old_format_pipeline(team)
        archived = Node.objects.create(pipeline=pipeline, flow_id="start-1", type="StartNode", is_archived=True)

        strip_node_data_from_pipelines(Pipeline, Node)

        archived.refresh_from_db()
        assert archived.position is None
        assert pipeline.node_set.get(flow_id="start-1").position == {"x": 0, "y": 0}


@pytest.mark.django_db()
class TestRebuildNodeData:
    """The reverse of the strip: rebuild the embedded blobs from the Node rows so that
    pre-ADR-0046 code (which requires them) works again after a code rollback."""

    def test_rebuilds_blobs_from_rows(self, team):
        pipeline = _create_old_format_pipeline(team)
        strip_node_data_from_pipelines(Pipeline, Node)

        rebuild_node_data_in_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        nodes_by_id = {node["id"]: node for node in pipeline.data["nodes"]}
        assert nodes_by_id["start-1"]["data"] == {
            "id": "start-1",
            "type": "StartNode",
            "label": "",
            "params": {"name": "start"},
        }
        assert nodes_by_id["start-1"]["position"] == {"x": 0, "y": 0}
        assert pipeline.data["edges"] == _old_format_data()["edges"]
        assert pipeline.data["viewport"] == _old_format_data()["viewport"]

    def test_leaves_nodes_without_rows_untouched(self, team):
        pipeline = Pipeline.objects.create(
            team=team, name="no-rows", data={"nodes": [{"id": "ghost", "type": "pipelineNode"}], "edges": []}
        )

        rebuild_node_data_in_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data["nodes"] == [{"id": "ghost", "type": "pipelineNode"}]

    def test_is_idempotent(self, team):
        pipeline = _create_old_format_pipeline(team)
        strip_node_data_from_pipelines(Pipeline, Node)

        rebuild_node_data_in_pipelines(Pipeline, Node)
        pipeline.refresh_from_db()
        first_pass = pipeline.data

        rebuild_node_data_in_pipelines(Pipeline, Node)
        pipeline.refresh_from_db()
        assert pipeline.data == first_pass


@pytest.mark.django_db()
@pytest.mark.usefixtures("unapplied_migration")
class TestStripNodeDataCommand:
    """The command backs migration ``pipelines.0030_strip_node_data``, so it runs through
    IdempotentCommand's run-once marker."""

    def test_strips_blobs(self, team):
        pipeline = _create_old_format_pipeline(team)

        call_command("strip_node_data")

        pipeline.refresh_from_db()
        assert "nodes" not in pipeline.data

    def test_records_the_migration_as_applied(self, team):
        _create_old_format_pipeline(team)

        call_command("strip_node_data")

        assert is_migration_applied(MIGRATION_NAME)

    def test_second_run_is_skipped(self, team):
        call_command("strip_node_data")
        pipeline = _create_old_format_pipeline(team)

        call_command("strip_node_data")

        pipeline.refresh_from_db()
        assert pipeline.data == _old_format_data()

    def test_force_reruns_after_the_marker_is_set(self, team):
        call_command("strip_node_data")
        pipeline = _create_old_format_pipeline(team)

        call_command("strip_node_data", "--force")

        pipeline.refresh_from_db()
        assert "nodes" not in pipeline.data


@pytest.mark.django_db()
class TestMigration0030:
    """Forward strips via the command, reverse rebuilds the nodes list from the rows."""

    def test_ran_during_database_setup(self):
        """Building the test database applies the migration, which runs the command. Fails
        if the migration is dropped or stops reaching the command."""
        assert is_migration_applied(MIGRATION_NAME)

    def test_is_reversible(self):
        """A RunDataMigration operation would make unapply raise IrreversibleError, so the
        forward has to be RunPython for the reverse to be reachable at all."""
        assert all(operation.reversible for operation in migration_0030.Migration.operations)

    def test_reverse_rebuilds_node_data(self, team):
        pipeline = _create_old_format_pipeline(team)
        strip_node_data_from_pipelines(Pipeline, Node)

        migration_0030.rebuild_node_data(django_apps, None)

        pipeline.refresh_from_db()
        nodes_by_id = {node["id"]: node for node in pipeline.data["nodes"]}
        assert nodes_by_id["start-1"]["data"]["type"] == "StartNode"
        assert nodes_by_id["start-1"]["position"] == {"x": 0, "y": 0}
        assert pipeline.data["edges"] == _old_format_data()["edges"]

    def test_reverse_works_with_historical_models(self, team):
        """What ``migrate pipelines 0029`` actually passes the reverse. Historical models
        have no properties or custom managers, so the helper must stick to columns and
        ``_base_manager``."""
        pipeline = _create_old_format_pipeline(team)
        strip_node_data_from_pipelines(Pipeline, Node)
        historical_apps = MigrationExecutor(connection).loader.project_state(("pipelines", "0030_strip_node_data")).apps

        migration_0030.rebuild_node_data(historical_apps, None)

        pipeline.refresh_from_db()
        assert {node["id"] for node in pipeline.data["nodes"]} == {"start-1", "end-1"}
