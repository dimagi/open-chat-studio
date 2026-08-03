"""Tests for the pipeline build-state helpers: the three-bucket errors report and ``pipeline_valid``."""

import pytest

from apps.pipelines.build_state import pipeline_build_state
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.tests.utils import (
    create_pipeline_model,
    end_node,
    passthrough_node,
    start_node,
)


class TestNodeValidationErrors:
    """How one node's pydantic errors are keyed by field."""

    def test_field_error_is_keyed_by_its_field(self):
        node = Node(flow_id="router-1", type="StaticRouterNode", params={"name": "router", "keywords": ["a"]})
        assert "route_key" in Pipeline._node_validation_errors(node)


@pytest.mark.django_db()
class TestPipelineBuildState:
    def test_fully_wired_pipeline_is_valid(self):
        start, end = start_node(), end_node()
        pipeline = create_pipeline_model([start, end])

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": []},
        }

    def test_missing_required_param_reports_node_error(self):
        start, end = start_node(), end_node()
        llm = {"id": "llm-1", "type": "LLMResponseWithPrompt", "params": {"name": "llm-1"}}
        edges = [
            {"id": "e1", "source": start["id"], "target": "llm-1"},
            {"id": "e2", "source": "llm-1", "target": end["id"]},
        ]
        pipeline = create_pipeline_model([start, llm, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {
                    "llm-1": {
                        "llm_provider_id": "Field required",
                        "llm_provider_model_id": "Field required",
                    }
                },
                "edge": [],
                "pipeline": [],
            },
        }

    def test_node_and_graph_errors_are_reported_together(self):
        """A bad node param no longer hides the graph's own problems: the missing provider id and
        the missing Start node arrive in the same report, so the client sees both in one read."""
        end = end_node()
        llm = {"id": "llm-1", "type": "LLMResponseWithPrompt", "params": {"name": "llm-1"}}
        edges = [{"id": "e-llm-end", "source": llm["id"], "target": end["id"]}]
        pipeline = create_pipeline_model([llm, end], edges)

        state = pipeline_build_state(pipeline)

        assert state["pipeline_valid"] is False
        assert "llm_provider_id" in state["errors"]["node"]["llm-1"]
        assert state["errors"]["pipeline"] == ["There should be exactly 1 Start node"]

    def test_every_graph_level_error_is_reported_not_just_the_first(self):
        """Independent structural checks accumulate rather than short-circuiting."""
        plain = passthrough_node(name="plain")
        pipeline = create_pipeline_model([plain], edges=[])

        state = pipeline_build_state(pipeline)

        assert state["errors"]["pipeline"] == [
            "There should be exactly 1 Start node",
            "There should be exactly 1 End node",
        ]

    def test_unreachable_end_is_an_error(self):
        # The build raises this one with the End node's id, so it normalizes into the node bucket
        # under the "root" sentinel rather than the pipeline bucket.
        start, island, end = start_node(), passthrough_node(), end_node()
        edges = [{"id": "e-start-island", "source": start["id"], "target": island["id"]}]
        pipeline = create_pipeline_model([start, island, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {end["id"]: {"root": "End node is not reachable from Start node"}},
                "edge": [],
                "pipeline": [],
            },
        }
