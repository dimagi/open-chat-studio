"""A pipeline holding a node type whose class has been removed.

``AssistantNode`` was deleted with the OpenAI Assistants feature (#4254), but rows of
``type="AssistantNode"`` still exist in ``node_set``. Those pipelines must keep opening in the
editor and must refuse to build. The two halves are deliberately separate mechanisms:

* ``resolve_node_class`` returns ``None`` for the stored type, so validation reports it and the
  graph will not compile. This is the pre-existing "unknown node type" path.
* ``get_node_schemas`` serves a stub schema for the type anyway, so the builder has something to
  render where the node used to be. The stub is built directly rather than from a node class, which
  is what keeps the first bullet true.
"""

import pytest

from apps.pipelines.exceptions import has_errors
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import resolve_node_class
from apps.pipelines.nodes.node_metadata import REMOVED_NODE_TYPES, get_node_schemas
from apps.pipelines.tests.utils import create_pipeline_model, end_node, start_node

REMOVED_TYPE = "AssistantNode"


def _pipeline_with_removed_node():
    """A saved pipeline whose middle node names a type that no longer has a class."""
    removed = {
        "id": "removed-1",
        "type": REMOVED_TYPE,
        "params": {"name": "My assistant step", "assistant_id": "42", "citations_enabled": True},
    }
    pipeline = create_pipeline_model([start_node(), removed, end_node()])
    pipeline.save(update_fields=["data"])
    return pipeline, removed


@pytest.mark.django_db()
class TestRemovedNodeIsNotRunnable:
    def test_stored_type_resolves_to_no_class(self):
        assert resolve_node_class(REMOVED_TYPE) is None

    def test_validation_reports_the_node_as_unknown(self):
        pipeline, removed = _pipeline_with_removed_node()

        report = pipeline.validate()

        assert has_errors(report), "a pipeline holding a removed node type must not be valid"
        assert report["node"][removed["id"]]["root"] == f"Unknown node type: {REMOVED_TYPE}"

    def test_the_node_row_survives_untouched(self):
        """Nothing rewrites or drops the row — Phase 2 owns the data cleanup, not this code."""
        pipeline, removed = _pipeline_with_removed_node()

        node = Node.objects.get(pipeline=pipeline, flow_id=removed["id"])
        assert node.type == REMOVED_TYPE
        assert node.params["assistant_id"] == "42"

    def test_node_reports_no_parameters(self):
        """``has_parameter`` is driven by the node class, so a removed type declares nothing."""
        assert Node(type=REMOVED_TYPE).has_parameter("assistant_id") is False


@pytest.mark.django_db()
class TestRemovedNodeStillLoadsInTheEditor:
    def test_flow_data_renders_the_node(self):
        """The editor is served ``flow_data``; it must include the node rather than raise."""
        pipeline, removed = _pipeline_with_removed_node()

        flow_nodes = {node["id"]: node for node in pipeline.flow_data["nodes"]}

        assert removed["id"] in flow_nodes
        assert flow_nodes[removed["id"]]["data"]["type"] == REMOVED_TYPE

    def test_a_stub_schema_is_served_for_the_removed_type(self):
        """Without this the builder's ``nodeSchemas.get(type)`` is undefined and the editor throws."""
        schemas = {schema["title"]: schema for schema in get_node_schemas()}

        assert REMOVED_TYPE in schemas, "the builder needs a schema for every stored node type"
        stub = schemas[REMOVED_TYPE]
        assert stub["ui:label"] == "Removed Node"
        assert stub["ui:removed"] is True
        assert stub["ui:can_add"] is False, "a removed type must not be offered in the palette"
        assert stub["ui:can_delete"] is True, "the user has to be able to delete it"
        assert stub["properties"] == {}, "a stub has no editable params"

    def test_the_stub_carries_a_deprecation_message(self):
        """The existing DeprecationNotice component renders this; no new UI needed for the text."""
        stub = next(s for s in get_node_schemas() if s["title"] == REMOVED_TYPE)

        assert stub["ui:deprecated"] is True
        assert stub["ui:deprecation_message"], "the node must explain itself to whoever opens it"

    def test_live_node_types_get_no_stub_treatment(self):
        """Only types in the registry are stubbed; everything else keeps its real schema."""
        schemas = {schema["title"]: schema for schema in get_node_schemas()}

        assert "LLMResponseWithPrompt" not in REMOVED_NODE_TYPES
        assert schemas["LLMResponseWithPrompt"].get("ui:removed", False) is False
        assert schemas["LLMResponseWithPrompt"]["properties"] != {}

    def test_registry_lists_the_assistant_node(self):
        assert REMOVED_TYPE in REMOVED_NODE_TYPES
