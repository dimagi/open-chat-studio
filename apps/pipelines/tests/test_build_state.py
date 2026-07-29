"""Tests for the pipeline build-state helpers: the normalized three-bucket errors report,
``pipeline_valid``, the advisory ``unwired_handles`` map, and the stranded-router-edge guard."""

import pytest

from apps.pipelines.build_state import (
    node_output_handles,
    normalize_errors,
    pipeline_build_state,
    unwired_handles,
)
from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.models import Node
from apps.pipelines.tests.utils import (
    create_pipeline_model,
    end_node,
    passthrough_node,
    start_node,
    state_key_router_node,
)


class TestNormalizeErrors:
    """Every partial shape ``Pipeline.validate()`` can return folds into the three-bucket report."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param({}, {"node": {}, "edge": [], "pipeline": None}, id="empty-report"),
            pytest.param(None, {"node": {}, "edge": [], "pipeline": None}, id="none-report"),
            pytest.param(
                {"node": {"llm-1": {"llm_provider_id": "This field is required."}}},
                {
                    "node": {"llm-1": {"llm_provider_id": "This field is required."}},
                    "edge": [],
                    "pipeline": None,
                },
                id="node-errors",
            ),
            pytest.param(
                PipelineBuildError("boom", node_id="n1").to_json(),
                {"node": {"n1": {"root": "boom"}}, "edge": [], "pipeline": None},
                id="build-error-with-node-id-keeps-root-sentinel-and-null-edge",
            ),
            pytest.param(
                PipelineBuildError("A cycle was detected").to_json(),
                {"node": {}, "edge": [], "pipeline": "A cycle was detected"},
                id="build-error-graph-level",
            ),
            pytest.param(
                PipelineBuildError("stranded", edge_ids=["edge-1", "edge-2"]).to_json(),
                {"node": {}, "edge": ["edge-1", "edge-2"], "pipeline": "stranded"},
                id="build-error-with-edge-ids",
            ),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_errors(raw) == expected


class TestNodeOutputHandles:
    def test_start_node_has_an_output_handle(self):
        node = Node(flow_id="start-1", type="StartNode", params={"name": "start"})
        assert node_output_handles(node) == [{"handle": "output", "label": None}]

    def test_end_node_has_no_output_handles(self):
        node = Node(flow_id="end-1", type="EndNode", params={"name": "end"})
        assert node_output_handles(node) == []

    def test_router_handles_come_from_keywords_in_order_upper_cased(self):
        node = Node(
            flow_id="router-1",
            type="StaticRouterNode",
            params={"name": "router", "route_key": "k", "keywords": ["schedule", "reschedule"]},
        )
        assert node_output_handles(node) == [
            {"handle": "output_0", "label": "SCHEDULE"},
            {"handle": "output_1", "label": "RESCHEDULE"},
        ]

    def test_invalid_router_still_reports_handles(self):
        # route_key is required, so full pydantic validation fails; the handles must still derive
        # from the keywords (upper-cased) so an incrementally-built router shows its branches.
        node = Node(
            flow_id="router-1",
            type="StaticRouterNode",
            params={"name": "router", "keywords": ["a", "b"]},
        )
        assert node_output_handles(node) == [
            {"handle": "output_0", "label": "A"},
            {"handle": "output_1", "label": "B"},
        ]

    @pytest.mark.django_db()
    def test_router_with_dangling_provider_model_still_reports_handles(self):
        # A stale llm_provider_model_id makes the LLM mixin's before-validator raise
        # PipelineNodeBuildError (not a pydantic error); handle derivation must fall back, not crash.
        node = Node(
            flow_id="router-1",
            type="RouterNode",
            params={
                "name": "router",
                "prompt": "route",
                "keywords": ["a", "b"],
                "llm_provider_id": 999999,
                "llm_provider_model_id": 999999,
            },
        )
        assert node_output_handles(node) == [
            {"handle": "output_0", "label": "A"},
            {"handle": "output_1", "label": "B"},
        ]

    def test_unknown_node_type_has_no_output_handles(self):
        node = Node(flow_id="ghost-1", type="GhostNode", params={"name": "ghost"})
        assert node_output_handles(node) == []

    def test_boolean_node_handles_are_static(self):
        node = Node(flow_id="bool-1", type="BooleanNode", params={"name": "bool", "input_equals": "hi"})
        assert node_output_handles(node) == [
            {"handle": "output_0", "label": "true"},
            {"handle": "output_1", "label": "false"},
        ]


@pytest.mark.django_db()
class TestPipelineBuildState:
    def test_fully_wired_pipeline_has_no_unwired_handles_and_is_valid(self):
        start, end = start_node(), end_node()
        pipeline = create_pipeline_model([start, end])

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": None},
            "unwired_handles": {},
        }

    def test_dangling_router_branch_is_advisory_not_an_error(self):
        """A valid graph with an unwired router branch stays pipeline_valid; the branch shows up
        only in unwired_handles, with its keyword as the label."""
        start, router, end = start_node(), state_key_router_node("k", ["A", "B"]), end_node()
        edges = [
            {"id": "e-start-router", "source": start["id"], "target": router["id"]},
            {"id": "e-router-end", "source": router["id"], "target": end["id"], "sourceHandle": "output_0"},
        ]
        pipeline = create_pipeline_model([start, router, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": None},
            "unwired_handles": {router["id"]: [{"handle": "output_1", "label": "B"}]},
        }

    def test_off_graph_island_reports_input_and_output_unwired(self):
        start, island, end = start_node(), passthrough_node(), end_node()
        edges = [{"id": "e-start-end", "source": start["id"], "target": end["id"]}]
        pipeline = create_pipeline_model([start, island, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": None},
            "unwired_handles": {
                island["id"]: [{"handle": "input", "label": None}, {"handle": "output", "label": None}]
            },
        }

    def test_start_input_and_end_output_are_never_reported(self):
        start, end = start_node(), end_node()
        pipeline = create_pipeline_model([start, end], edges=[])

        assert unwired_handles(pipeline) == {
            start["id"]: [{"handle": "output", "label": None}],
            end["id"]: [{"handle": "input", "label": None}],
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
                "pipeline": None,
            },
            "unwired_handles": {},
        }

    def test_dangling_provider_model_reference_reports_node_error_instead_of_raising(self):
        """A node whose params reference a deleted LlmProviderModel raises PipelineNodeBuildError
        from inside pydantic validation; the build state must fold it into errors.node, not 500."""
        start, end = start_node(), end_node()
        llm = {
            "id": "llm-1",
            "type": "LLMResponseWithPrompt",
            "params": {
                "name": "llm-1",
                "prompt": "hi",
                "llm_provider_id": 999999,
                "llm_provider_model_id": 999999,
            },
        }
        edges = [
            {"id": "e1", "source": start["id"], "target": "llm-1"},
            {"id": "e2", "source": "llm-1", "target": end["id"]},
        ]
        pipeline = create_pipeline_model([start, llm, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {"llm-1": {"root": "LLM provider model with id 999999 does not exist"}},
                "edge": [],
                "pipeline": None,
            },
            "unwired_handles": {},
        }

    def test_unknown_node_type_reports_node_error_instead_of_raising(self):
        start, end = start_node(), end_node()
        ghost = {"id": "ghost-1", "type": "GhostNode", "params": {"name": "ghost"}}
        pipeline = create_pipeline_model([start, ghost, end], edges=[])

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {"ghost-1": {"root": "Unknown node type: GhostNode"}},
                "edge": [],
                "pipeline": None,
            },
            "unwired_handles": {
                start["id"]: [{"handle": "output", "label": None}],
                # An unknown type's outputs are unknowable, so only its implicit input is reported.
                ghost["id"]: [{"handle": "input", "label": None}],
                end["id"]: [{"handle": "input", "label": None}],
            },
        }

    def test_stranded_router_edge_lands_in_edge_bucket_without_raising(self):
        """Removing a router keyword strands the edge wired to its handle: the build must report
        the edge id in errors.edge (not raise a KeyError) and flip pipeline_valid."""
        start, router, end = start_node(), state_key_router_node("k", ["A", "B"]), end_node()
        edges = [
            {"id": "e-start-router", "source": start["id"], "target": router["id"]},
            {"id": "e-router-end", "source": router["id"], "target": end["id"], "sourceHandle": "output_1"},
        ]
        pipeline = create_pipeline_model([start, router, end], edges)

        router["params"]["keywords"] = ["A"]
        create_pipeline_model([start, router, end], edges, pipeline=pipeline)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {},
                "edge": ["e-router-end"],
                "pipeline": "One or more edges reference a router output that no longer exists",
            },
            "unwired_handles": {router["id"]: [{"handle": "output_0", "label": "A"}]},
        }

    def test_stranded_edge_on_unreachable_router_does_not_block_the_build(self):
        """A stranded conditional edge only blocks the build when its router is reachable — an
        off-graph island's stranded edge stays the advisory unwired map's concern."""
        start, end = start_node(), end_node()
        router = state_key_router_node("k", ["A", "B"], name="main-router")
        island_router = state_key_router_node("k2", ["A"], name="island-router")
        island_target = passthrough_node(name="island-target")
        edges = [
            {"id": "e-start-router", "source": start["id"], "target": router["id"]},
            {"id": "e-router-end-0", "source": router["id"], "target": end["id"], "sourceHandle": "output_0"},
            {"id": "e-router-end-1", "source": router["id"], "target": end["id"], "sourceHandle": "output_1"},
            # output_1 does not exist on the island router (it only has one keyword) — stranded.
            {
                "id": "e-island-stranded",
                "source": island_router["id"],
                "target": island_target["id"],
                "sourceHandle": "output_1",
            },
        ]
        pipeline = create_pipeline_model([start, router, end, island_router, island_target], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": None},
            "unwired_handles": {
                island_router["id"]: [
                    {"handle": "input", "label": None},
                    {"handle": "output_0", "label": "A"},
                ],
                island_target["id"]: [{"handle": "output", "label": None}],
            },
        }

    def test_unreachable_end_is_an_error_but_still_reports_unwired_map(self):
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
                "pipeline": None,
            },
            "unwired_handles": {
                island["id"]: [{"handle": "output", "label": None}],
                end["id"]: [{"handle": "input", "label": None}],
            },
        }
