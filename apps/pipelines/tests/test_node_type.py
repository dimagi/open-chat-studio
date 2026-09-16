"""Tests for ``NodeType``: the questions a stored ``Node.type`` answers.

Mostly DB-free — the type string is all a ``NodeType`` holds — so only the one case that reaches a
resource lookup carries a ``django_db`` marker.
"""

from apps.pipelines.node_type import NodeType


class TestNodeClassResolution:
    def test_a_node_type_resolves_to_its_class(self):
        node_type = NodeType("LLMResponseWithPrompt")
        assert node_type.node_class is not None
        assert node_type.node_class.__name__ == "LLMResponseWithPrompt"

    def test_a_type_naming_no_class_does_not_exist(self):
        assert NodeType("GhostNode").node_class is None
