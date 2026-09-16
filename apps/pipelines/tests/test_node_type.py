"""Tests for ``NodeType``: the questions a stored ``Node.type`` answers.

Mostly DB-free — the type string is all a ``NodeType`` holds — so only the one case that reaches a
resource lookup carries a ``django_db`` marker.
"""

import pytest

from apps.pipelines.node_type import NodeType


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
