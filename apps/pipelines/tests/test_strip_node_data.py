from importlib import import_module

import pytest
from django.apps import apps as global_apps

from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)
from apps.pipelines.models import Node, Pipeline

migration_0030 = import_module("apps.pipelines.migrations.0030_strip_node_data")


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
    def test_drops_legacy_keys_and_preserves_edges(self, team):
        pipeline = _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == {"edges": _old_format_data()["edges"]}
        # rows untouched
        assert pipeline.node_set.get(flow_id="start-1").params == {"name": "start"}

    def test_drops_a_viewport_from_already_stripped_data(self, team):
        """``viewport`` is modelled nowhere, so a blob that has already lost its ``nodes`` key
        still gets it removed rather than waiting for the pipeline's next save."""
        pipeline = Pipeline.objects.create(
            team=team, name="stripped", data={"edges": [], "viewport": {"x": 9, "y": 9, "zoom": 2}}
        )

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == {"edges": []}

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
        never destroy it; skip and log so it can be healed manually. The skip is wholesale, so
        the pipeline keeps its ``viewport`` too: one flagged for healing is left as found."""
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


@pytest.mark.django_db()
class TestBackfillPositions:
    """The strip also mirrors each blob node's position onto the row's position columns,
    which is what makes the rows a complete layout source for reads (ADR-0049)."""

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

    def test_reads_serve_backfilled_positions_instead_of_the_origin(self, team):
        """Why the backfill must ship with the read switch (ADR-0049): until it runs, a row
        with NULL columns reads back at the origin, and the first save of that pipeline drops
        the blob holding the only copy of its real layout."""
        pipeline = _create_old_format_pipeline(team)

        assert {node["id"]: node["position"] for node in pipeline.flow_data["nodes"]} == {
            "start-1": {"x": 0, "y": 0},
            "end-1": {"x": 0, "y": 0},  # really at x=100 in the blob, served as the origin
        }

        strip_node_data_from_pipelines(Pipeline, Node)

        del pipeline.flow_data  # cached off the pre-backfill rows
        assert {node["id"]: node["position"] for node in pipeline.flow_data["nodes"]} == {
            "start-1": {"x": 0, "y": 0},
            "end-1": {"x": 100, "y": 0},
        }

    @pytest.mark.parametrize(
        ("position_x", "position_y"),
        [
            pytest.param(999, 888, id="off-axis"),
            pytest.param(0, 50, id="on-the-y-axis"),
            pytest.param(50, 0, id="on-the-x-axis"),
            pytest.param(0, 0, id="at-the-origin"),
        ],
    )
    def test_leaves_an_already_positioned_row_alone(self, team, position_x, position_y):
        """A row with both columns set is the live layout — the shadow-write has been running
        since the columns shipped, so the blob may predate the last move. Zero is a real
        coordinate here, not a missing one."""
        pipeline = _create_old_format_pipeline(team)
        pipeline.node_set.filter(flow_id="end-1").update(position_x=position_x, position_y=position_y)

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="end-1").position == {"x": position_x, "y": position_y}
        # its unpositioned sibling is still backfilled from the blob
        assert pipeline.node_set.get(flow_id="start-1").position == {"x": 0, "y": 0}

    @pytest.mark.parametrize(
        ("position_x", "position_y"),
        [
            pytest.param(999, None, id="only-x-set"),
            pytest.param(None, 888, id="only-y-set"),
        ],
    )
    def test_backfills_a_half_positioned_row(self, team, position_x, position_y):
        """One NULL column reads back as no position at all, so the blob still fills it."""
        pipeline = _create_old_format_pipeline(team)
        pipeline.node_set.filter(flow_id="end-1").update(position_x=position_x, position_y=position_y)

        strip_node_data_from_pipelines(Pipeline, Node)

        assert pipeline.node_set.get(flow_id="end-1").position == {"x": 100, "y": 0}

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
    pre-ADR-0049 code (which requires them) works again after a code rollback."""

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

    def test_does_not_restore_the_stripped_viewport(self, team):
        """The forward strip drops ``viewport`` for good. Not a gap in the rollback guarantee:
        no code on either side of ADR-0049 reads it."""
        pipeline = _create_old_format_pipeline(team)
        strip_node_data_from_pipelines(Pipeline, Node)

        rebuild_node_data_in_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert "viewport" not in pipeline.data
        # the graph pre-ADR-0049 code does read is whole again
        assert len(pipeline.data["nodes"]) == 2
        assert pipeline.data["edges"] == _old_format_data()["edges"]

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
class TestMigration0030:
    """The migration's own RunPython callables, which is where the rollback guarantee lives.

    Exercises the wiring — model lookups and forward/reverse argument passing — rather than
    Django's apply/unapply machinery, so it does not touch recorded migration state.
    """

    def test_forward_then_reverse_round_trips(self, team):
        pipeline = _create_old_format_pipeline(team)

        migration_0030.strip_node_data(global_apps, None)

        pipeline.refresh_from_db()
        assert "nodes" not in pipeline.data
        assert "viewport" not in pipeline.data
        assert pipeline.node_set.get(flow_id="end-1").position == {"x": 100, "y": 0}

        migration_0030.rebuild_node_data(global_apps, None)

        pipeline.refresh_from_db()
        rebuilt = {node["id"]: node for node in pipeline.data["nodes"]}
        assert rebuilt["end-1"]["position"] == {"x": 100, "y": 0}
        assert rebuilt["end-1"]["data"]["params"] == {"name": "end"}
        assert rebuilt["end-1"]["type"] == "endNode"
        # the reverse must restore what pre-ADR-0049 code requires: a parseable full graph.
        # ``viewport`` is not part of that — nothing on either side of ADR-0049 reads it.
        assert pipeline.data["edges"] == _old_format_data()["edges"]
