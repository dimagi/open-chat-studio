"""A pipeline holding a node type whose class has been removed.

When a node class is deleted, rows of that ``type`` stay behind in ``node_set``. Those pipelines
must keep opening in the editor and must refuse to build. The two halves are deliberately separate
mechanisms:

* ``resolve_node_class`` returns ``None`` for the stored type, so validation reports it and the
  graph will not compile. This is the pre-existing "unknown node type" path.
* ``get_node_schemas`` serves a stub schema for every type in ``REMOVED_NODE_TYPES`` anyway, so the
  builder has something to render where the node used to be. The stub is built directly rather than
  from a node class, which is what keeps the first bullet true.

Most of these tests register a type of their own rather than depending on whichever types happen
to be listed; the last one covers ``AssistantNode``, the first real entry (#4254).
"""

from unittest import mock

import pytest

from apps.pipelines.exceptions import has_errors
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import resolve_node_class
from apps.pipelines.nodes.node_metadata import REMOVED_NODE_TYPES, get_node_schemas
from apps.pipelines.tests.utils import create_pipeline_model, end_node, start_node

REMOVED_TYPE = "RetiredExampleNode"
REMOVED_MESSAGE = "This node type was removed. Delete it and use an LLM node instead."


@pytest.fixture()
def registered_as_removed():
    """``REMOVED_TYPE`` is the only entry in the registry for the duration of a test."""
    with mock.patch.dict(REMOVED_NODE_TYPES, {REMOVED_TYPE: REMOVED_MESSAGE}, clear=True):
        yield


def _pipeline_with_removed_node():
    """A saved pipeline whose middle node names a type that no longer has a class."""
    removed = {
        "id": "removed-1",
        "type": REMOVED_TYPE,
        "params": {"name": "My retired step", "some_param": "42"},
    }
    pipeline = create_pipeline_model([start_node(), removed, end_node()])
    pipeline.save(update_fields=["data"])
    return pipeline, removed


@pytest.mark.django_db()
class TestRemovedNodeIsNotRunnable:
    def test_stored_type_resolves_to_no_class(self):
        assert resolve_node_class(REMOVED_TYPE) is None

    def test_validation_reports_the_node_as_unknown(self, registered_as_removed):
        """Serving a stub schema must not make the pipeline buildable."""
        pipeline, removed = _pipeline_with_removed_node()

        report = pipeline.validate()

        assert has_errors(report), "a pipeline holding a removed node type must not be valid"
        assert report["node"][removed["id"]]["root"] == f"Unknown node type: {REMOVED_TYPE}"

    def test_the_node_row_survives_untouched(self):
        """Nothing rewrites or drops the row — data cleanup is a separate, later decision."""
        pipeline, removed = _pipeline_with_removed_node()

        node = Node.objects.get(pipeline=pipeline, flow_id=removed["id"])
        assert node.type == REMOVED_TYPE
        assert node.params["some_param"] == "42"

    def test_node_reports_no_parameters(self):
        """``has_parameter`` is driven by the node class, so a removed type declares nothing."""
        assert Node(type=REMOVED_TYPE).has_parameter("some_param") is False


@pytest.mark.django_db()
class TestRemovedNodeStillLoadsInTheEditor:
    def test_flow_data_renders_the_node(self):
        """The editor is served ``flow_data``; it must include the node rather than raise."""
        pipeline, removed = _pipeline_with_removed_node()

        flow_nodes = {node["id"]: node for node in pipeline.flow_data["nodes"]}

        assert removed["id"] in flow_nodes
        assert flow_nodes[removed["id"]]["data"]["type"] == REMOVED_TYPE

    def test_a_stub_schema_is_served_for_the_removed_type(self, registered_as_removed):
        """Without this the builder's ``nodeSchemas.get(type)`` is undefined and the editor throws."""
        schemas = {schema["title"]: schema for schema in get_node_schemas()}

        assert REMOVED_TYPE in schemas, "the builder needs a schema for every stored node type"
        stub = schemas[REMOVED_TYPE]
        assert stub["ui:label"] == "Removed Node"
        assert stub["ui:removed"] is True
        assert stub["ui:can_add"] is False, "a removed type must not be offered in the palette"
        assert stub["ui:can_delete"] is True, "the user has to be able to delete it"
        assert stub["properties"] == {}, "a stub has no editable params"

    def test_the_stub_carries_a_deprecation_message(self, registered_as_removed):
        """The existing DeprecationNotice component renders this; no new UI needed for the text."""
        stub = next(s for s in get_node_schemas() if s["title"] == REMOVED_TYPE)

        assert stub["ui:deprecated"] is True
        assert stub["ui:deprecation_message"] == REMOVED_MESSAGE

    def test_live_node_types_get_no_stub_treatment(self, registered_as_removed):
        """Only types in the registry are stubbed; everything else keeps its real schema."""
        schemas = {schema["title"]: schema for schema in get_node_schemas()}

        assert "LLMResponseWithPrompt" not in REMOVED_NODE_TYPES
        assert schemas["LLMResponseWithPrompt"].get("ui:removed", False) is False
        assert schemas["LLMResponseWithPrompt"]["properties"] != {}

    def test_the_assistant_node_is_served_as_a_stub(self):
        """The first real user of the registry: AssistantNode lost its class with #4254, and
        pipelines still holding one have to open."""
        assert "AssistantNode" in REMOVED_NODE_TYPES

        stub = next(s for s in get_node_schemas() if s["title"] == "AssistantNode")

        assert stub["ui:removed"] is True
        assert stub["properties"] == {}
        assert resolve_node_class("AssistantNode") is None, "it must still refuse to build"
