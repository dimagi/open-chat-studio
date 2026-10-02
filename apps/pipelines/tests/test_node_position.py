import pytest

from apps.pipelines.models import Node
from apps.pipelines.tests.utils import content_flow_node, create_pipeline_model, end_node, start_node
from apps.utils.factories.pipelines import PipelineFactory


@pytest.mark.django_db()
class TestNodePosition:
    """Where a node sits on the canvas: the ``Node`` position columns are the only source of
    it (ADR-0049), so a save has to write them and a read has to serve a usable pair."""

    def test_position_is_written_to_the_row(self):
        """A mapping entry's position lands on the row's position columns (floats kept
        verbatim); the columns are the authoritative layout source for reads (ADR-0049)."""
        pipeline = PipelineFactory.create()
        pipeline.data = {"edges": []}
        pipeline.update_nodes_from_data(
            {"n1": content_flow_node("n1", "StartNode", params={"name": "start"}, position={"x": 10.7, "y": -3.2})}
        )

        node = Node.objects.get(pipeline=pipeline, flow_id="n1")
        assert node.position_x == 10.7
        assert node.position_y == -3.2
        assert node.position == {"x": 10.7, "y": -3.2}

    @pytest.mark.parametrize(
        "position",
        [
            pytest.param({}, id="absent"),
            pytest.param({"x": "abc", "y": 2}, id="non-numeric"),
            pytest.param({"x": 1}, id="missing-axis"),
        ],
    )
    @pytest.mark.parametrize(
        ("placed_at", "expected"),
        [
            pytest.param(None, {"x": 0, "y": 0}, id="new-row"),
            pytest.param({"x": 42.5, "y": -7.0}, {"x": 42.5, "y": -7.0}, id="placed-row"),
        ],
    )
    def test_unusable_position_never_moves_the_row(self, position, placed_at, expected):
        """Raw import files bypass wire validation, so a bad position must neither crash the save
        nor write garbage. It leaves the row where it is: a new one at the column default, a
        placed one on its live coordinates rather than dragged back to the origin.
        """
        pipeline = PipelineFactory.create()
        pipeline.data = {"edges": []}
        if placed_at:
            pipeline.update_nodes_from_data(
                {"n1": content_flow_node("n1", "StartNode", params={"name": "start"}, position=placed_at)}
            )

        pipeline.update_nodes_from_data(
            {"n1": content_flow_node("n1", "StartNode", params={"name": "start"}, position=position)}
        )

        node = Node.objects.get(pipeline=pipeline, flow_id="n1")
        assert node.position == expected

    def test_flow_data_serves_the_origin_for_a_row_saved_without_a_position(self):
        """A row must serve a real coordinate pair — react-flow does arithmetic on
        position.x/y, so an empty dict yields NaN layout that persists on the next save."""
        start, end = start_node(), end_node()
        pipeline = create_pipeline_model([start, end])
        # create_pipeline_model carries no positions, so the rows take the column default
        assert pipeline.node_set.get(flow_id=start["id"]).position == {"x": 0, "y": 0}

        nodes_by_id = {node["id"]: node for node in pipeline.flow_data["nodes"]}

        assert nodes_by_id[start["id"]]["position"] == {"x": 0, "y": 0}
