import pytest

from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)
from apps.pipelines.models import Node, Pipeline


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
    def test_strips_blobs_and_preserves_layout(self, team):
        pipeline = _create_old_format_pipeline(team)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data["nodes"] == [
            {"id": "start-1", "type": "startNode", "position": {"x": 0, "y": 0}},
            {"id": "end-1", "type": "endNode", "position": {"x": 100, "y": 0}},
        ]
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
        assert all("data" not in node for node in pipeline.data["nodes"])

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({}, id="empty-data"),
            pytest.param({"edges": []}, id="no-nodes-key"),
            pytest.param({"nodes": [{"id": "a"}], "edges": []}, id="node-without-position-or-type"),
            pytest.param({"nodes": [{"data": {"type": "StartNode"}}], "edges": []}, id="blob-node-without-id"),
        ],
    )
    def test_tolerates_degenerate_data(self, team, data):
        pipeline = Pipeline.objects.create(team=team, name="degenerate", data=data)

        strip_node_data_from_pipelines(Pipeline, Node)

        pipeline.refresh_from_db()
        assert pipeline.data == data


@pytest.mark.django_db()
class TestRebuildNodeData:
    """The reverse migration: rebuild the embedded blobs from the Node rows so that
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
