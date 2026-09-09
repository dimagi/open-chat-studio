"""Tests for ``NodeType``: the questions a stored ``Node.type`` answers.

Mostly DB-free — the type string is all a ``NodeType`` holds — so only the one case that reaches a
resource lookup carries a ``django_db`` marker.
"""

import pytest

from apps.pipelines.node_type import NodeType, NoOutputHandles, server_managed_node_types
from apps.pipelines.tests.utils import NON_NODE_ATTRIBUTES
from apps.pipelines.versioning import ParamVersioning


class TestNodeClassResolution:
    def test_a_node_type_resolves_to_its_class(self):
        node_type = NodeType("LLMResponseWithPrompt")
        assert node_type.exists is True
        assert node_type.node_class is not None
        assert node_type.node_class.__name__ == "LLMResponseWithPrompt"

    def test_a_type_naming_no_class_does_not_exist(self):
        assert NodeType("GhostNode").exists is False
        assert NodeType("GhostNode").node_class is None


class TestNullObject:
    """Every accessor answers for a type naming no node class rather than raising.

    ``Node.type`` is unvalidated graph data and ``REMOVED_NODE_TYPES`` makes an unresolvable type a
    supported state, so the empty answers are the contract callers rely on to stop branching.
    """

    @pytest.mark.parametrize("node_type", [*NON_NODE_ATTRIBUTES, pytest.param("GhostNode", id="no-such-attribute")])
    def test_whole_surface_answers(self, node_type):
        unresolvable = NodeType(node_type)

        assert unresolvable.exists is False
        assert unresolvable.node_class is None
        assert unresolvable.declared_params == frozenset()
        assert unresolvable.declares("name") is False
        assert unresolvable.schema is None
        assert unresolvable.is_server_managed is False
        assert unresolvable.versioned_param_specs == ()
        assert unresolvable.output_handles({"name": "odd"}, "odd-1") == []
        assert unresolvable.why_no_output_handles() is NoOutputHandles.UNKNOWN_TYPE
        assert unresolvable.input_handles() == ["input"]
        assert unresolvable.react_flow_type == "pipelineNode"
        assert unresolvable.render_order == 1


class TestDeclaredParams:
    @pytest.mark.parametrize(
        ("node_type", "param_name", "expected"),
        [
            pytest.param("LLMResponseWithPrompt", "llm_provider_id", True, id="declared"),
            pytest.param("LLMResponseWithPrompt", "route_key", False, id="not-declared"),
            pytest.param("RouterNode", "prompt", True, id="declared-on-other-type"),
            pytest.param("NoSuchNode", "assistant_id", False, id="unknown-node-type"),
        ],
    )
    def test_declares(self, node_type, param_name, expected):
        assert NodeType(node_type).declares(param_name) is expected

    def test_declared_params_lists_the_type_s_fields(self):
        declared = NodeType("LLMResponseWithPrompt").declared_params
        assert {"name", "llm_provider_id", "prompt"} <= declared


class TestSchema:
    def test_a_node_type_carries_its_ui_schema(self):
        schema = NodeType("LLMResponseWithPrompt").schema
        assert schema is not None
        assert schema.label

    @pytest.mark.parametrize(
        ("node_type", "expected"),
        [
            pytest.param("StartNode", True, id="start"),
            pytest.param("EndNode", True, id="end"),
            pytest.param("LLMResponseWithPrompt", False, id="regular"),
        ],
    )
    def test_is_server_managed_follows_can_delete(self, node_type, expected):
        assert NodeType(node_type).is_server_managed is expected

    def test_server_managed_node_types_is_exactly_the_types_that_report_it(self):
        assert server_managed_node_types() == {"StartNode", "EndNode"}


class TestReactFlowType:
    @pytest.mark.parametrize(
        ("node_type", "expected"),
        [
            pytest.param("StartNode", "startNode", id="start"),
            pytest.param("EndNode", "endNode", id="end"),
            pytest.param("LLMResponseWithPrompt", "pipelineNode", id="regular"),
            pytest.param("RenderTemplate", "pipelineNode", id="another-regular"),
        ],
    )
    def test_maps_node_type_to_react_flow_type(self, node_type, expected):
        assert NodeType(node_type).react_flow_type == expected


class TestRenderOrder:
    def test_pins_start_first_and_end_last(self):
        start, middle, end = NodeType("StartNode"), NodeType("LLMResponseWithPrompt"), NodeType("EndNode")
        assert start.render_order < middle.render_order < end.render_order


class TestVersionedParamSpecs:
    def test_a_type_with_referenced_records_lists_them(self):
        specs = NodeType("LLMResponseWithPrompt").versioned_param_specs
        assert {spec.param_name for spec in specs} >= {"source_material_id", "collection_id"}
        assert all(isinstance(spec.versioning, ParamVersioning) for spec in specs)

    def test_a_type_with_none_lists_none(self):
        assert NodeType("RenderTemplate").versioned_param_specs == ()


class TestInputHandles:
    def test_start_accepts_no_input(self):
        assert NodeType("StartNode").input_handles() == []

    @pytest.mark.parametrize("node_type", ["EndNode", "LLMResponseWithPrompt", "StaticRouterNode"])
    def test_every_other_type_accepts_the_standard_input(self, node_type):
        assert NodeType(node_type).input_handles() == ["input"]


class TestOutputHandles:
    def test_start_node_has_an_output_handle(self):
        assert NodeType("StartNode").output_handles({"name": "start"}, "start-1") == [
            {"handle": "output", "label": None}
        ]

    def test_end_node_has_no_output_handles(self):
        assert NodeType("EndNode").output_handles({"name": "end"}, "end-1") == []

    def test_router_handles_come_from_keywords_in_order_upper_cased(self):
        params = {"name": "router", "route_key": "k", "keywords": ["schedule", "reschedule"]}
        assert NodeType("StaticRouterNode").output_handles(params, "router-1") == [
            {"handle": "output_0", "label": "SCHEDULE"},
            {"handle": "output_1", "label": "RESCHEDULE"},
        ]

    def test_invalid_router_still_reports_handles(self):
        # route_key is required, so full pydantic validation fails; the handles must still derive
        # from the keywords (upper-cased) so an incrementally-built router shows its branches.
        params = {"name": "router", "keywords": ["a", "b"]}
        assert NodeType("StaticRouterNode").output_handles(params, "router-1") == [
            {"handle": "output_0", "label": "A"},
            {"handle": "output_1", "label": "B"},
        ]

    @pytest.mark.django_db()
    def test_router_with_dangling_provider_model_still_reports_handles(self):
        # A stale llm_provider_model_id makes the LLM mixin's before-validator raise
        # PipelineNodeBuildError (not a pydantic error); handle derivation must fall back, not crash.
        params = {
            "name": "router",
            "prompt": "route",
            "keywords": ["a", "b"],
            "llm_provider_id": 999999,
            "llm_provider_model_id": 999999,
        }
        assert NodeType("RouterNode").output_handles(params, "router-1") == [
            {"handle": "output_0", "label": "A"},
            {"handle": "output_1", "label": "B"},
        ]

    def test_boolean_node_handles_are_static(self):
        params = {"name": "bool", "input_equals": "hi"}
        assert NodeType("BooleanNode").output_handles(params, "bool-1") == [
            {"handle": "output_0", "label": "true"},
            {"handle": "output_1", "label": "false"},
        ]


class TestWhyNoOutputHandles:
    @pytest.mark.parametrize(
        ("node_type", "expected"),
        [
            pytest.param("EndNode", NoOutputHandles.TERMINAL, id="end"),
            pytest.param("GhostNode", NoOutputHandles.UNKNOWN_TYPE, id="unknown-type"),
            pytest.param("StaticRouterNode", NoOutputHandles.NO_BRANCHES, id="router-without-keywords"),
            pytest.param("LLMResponseWithPrompt", NoOutputHandles.UNDETERMINED, id="unrecognised"),
        ],
    )
    def test_names_the_empty_case(self, node_type, expected):
        assert NodeType(node_type).why_no_output_handles() is expected
