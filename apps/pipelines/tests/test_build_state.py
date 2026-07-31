"""Tests for the pipeline build-state helpers: the three-bucket errors report, ``pipeline_valid``,
the advisory ``unwired_handles`` map, and the stranded-router-edge guard."""

import pytest

from apps.pipelines.build_state import node_output_handles, pipeline_build_state, unwired_handles
from apps.pipelines.models import Node
from apps.pipelines.tests.utils import (
    create_pipeline_model,
    end_node,
    passthrough_node,
    start_node,
    state_key_router_node,
)


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
            "errors": {"node": {}, "edge": [], "pipeline": []},
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
            "errors": {"node": {}, "edge": [], "pipeline": []},
            "unwired_handles": {router["id"]: [{"handle": "output_1", "label": "B"}]},
        }

    def test_off_graph_island_reports_input_and_output_unwired(self):
        start, island, end = start_node(), passthrough_node(), end_node()
        edges = [{"id": "e-start-end", "source": start["id"], "target": end["id"]}]
        pipeline = create_pipeline_model([start, island, end], edges)

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": True,
            "errors": {"node": {}, "edge": [], "pipeline": []},
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
                "pipeline": [],
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
                "pipeline": [],
            },
            "unwired_handles": {},
        }

    def test_unknown_node_type_reports_node_error_instead_of_raising(self):
        """The unknown type is a node error; with no edges at all the End node is also unreachable.
        Both are reported together — a broken node no longer hides the graph's own problems."""
        start, end = start_node(), end_node()
        ghost = {"id": "ghost-1", "type": "GhostNode", "params": {"name": "ghost"}}
        pipeline = create_pipeline_model([start, ghost, end], edges=[])

        state = pipeline_build_state(pipeline)

        assert state == {
            "pipeline_valid": False,
            "errors": {
                "node": {
                    "ghost-1": {"root": "Unknown node type: GhostNode"},
                    end["id"]: {"root": "End node is not reachable from Start node"},
                },
                "edge": [],
                "pipeline": [],
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
                "pipeline": ["One or more edges reference a router output that no longer exists"],
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
            "errors": {"node": {}, "edge": [], "pipeline": []},
            "unwired_handles": {
                island_router["id"]: [
                    {"handle": "input", "label": None},
                    {"handle": "output_0", "label": "A"},
                ],
                island_target["id"]: [{"handle": "output", "label": None}],
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

    def test_invalid_router_does_not_have_its_edges_called_stranded(self):
        """A router whose params don't validate can't report its branches, so its handles are
        unknown — not empty. Its edges must not be reported stranded on the strength of that."""
        start, end = start_node(), end_node()
        router = state_key_router_node("k", ["A", "B"], name="router")
        del router["params"]["route_key"]  # required, so the node no longer validates
        edges = [
            {"id": "e-start-router", "source": start["id"], "target": router["id"]},
            {"id": "e-router-end", "source": router["id"], "target": end["id"], "sourceHandle": "output_0"},
        ]
        pipeline = create_pipeline_model([start, router, end], edges)

        state = pipeline_build_state(pipeline)

        assert state["pipeline_valid"] is False
        assert "route_key" in state["errors"]["node"][router["id"]]
        assert state["errors"]["edge"] == []

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
                "pipeline": [],
            },
            "unwired_handles": {
                island["id"]: [{"handle": "output", "label": None}],
                end["id"]: [{"handle": "input", "label": None}],
            },
        }
