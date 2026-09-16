"""Tests for ``NodeType``: the questions a stored ``Node.type`` answers.

Mostly DB-free — the type string is all a ``NodeType`` holds — so only the one case that reaches a
resource lookup carries a ``django_db`` marker.
"""

import pytest

from apps.pipelines.node_type import NodeType, server_managed_node_types


class TestNodeClassResolution:
    def test_a_node_type_resolves_to_its_class(self):
        node_type = NodeType("LLMResponseWithPrompt")
        assert node_type.exists is True
        assert node_type.node_class is not None
        assert node_type.node_class.__name__ == "LLMResponseWithPrompt"

    def test_a_type_naming_no_class_does_not_exist(self):
        assert NodeType("GhostNode").exists is False
        assert NodeType("GhostNode").node_class is None


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
