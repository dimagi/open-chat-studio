import json
import pathlib

import pytest

from apps.pipelines.nodes.node_metadata import get_node_schemas

BASE = pathlib.Path(__file__).parent / "node_schemas"


def _live_schemas():
    """Schemas for the node types the discovery API serves."""
    return [schema for schema in get_node_schemas() if not schema.get("ui:deprecated")]


@pytest.mark.parametrize("schema", _live_schemas(), ids=lambda schema: schema["title"])
def test_every_param_is_described(schema):
    """`title` is auto-derived from the field name ("Llm Provider Model Id"), so a param needs a
    `description` to say anything the key didn't already."""
    undescribed = [name for name, prop in schema["properties"].items() if not prop.get("description", "").strip()]
    assert not undescribed, (
        f"{schema['title']} params without a description: {undescribed}. "
        f"Add `description=` to the pydantic Field -- the pipeline builder shows it as help text and "
        f"/api/v2/pipeline/nodes/ serves it to agents."
    )


@pytest.mark.parametrize("schema", get_node_schemas(), ids=lambda schema: schema["title"])
def test_no_param_points_at_a_definition_that_was_dropped(schema):
    """`_get_node_schema` inlines the `$ref`s and then drops `$defs`, so a surviving `$ref` points at
    nothing a reader can follow. `resolve_references` substitutes one level deep, which leaves the
    inner `$ref` behind for a param whose model nests another model two deep -- add one and this
    fails rather than shipping a dangling pointer to the builder and to agents."""
    dangling = list(_reference_paths(schema))
    assert not dangling, (
        f"{schema['title']} keeps unresolved references at {dangling}. `$defs` is gone by then, so "
        f"nothing resolves them. Flatten the nesting, or teach `resolve_references` to recurse."
    )


def _reference_paths(node, path=""):
    """Every `$ref` left in `node`, by the path it sits at."""
    if isinstance(node, dict):
        if "$ref" in node:
            yield f"{path} -> {node['$ref']}"
        for key, value in node.items():
            yield from _reference_paths(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _reference_paths(value, f"{path}[{index}]")


def test_schemas():
    schemas = get_node_schemas()
    for schema in schemas:
        title = schema["title"]
        assert schema["description"], title
        assert schema["ui:label"], title

        path = BASE / f"{title}.json"
        if schema != json.loads(path.read_text()):
            raise AssertionError(
                f"Pipeline schema for {title} has changed. Run 'python manage.py update_pipeline_schema'."
            )


def test_pipeline_node_schemas():
    schemas = {schema["title"] for schema in get_node_schemas()}
    for file in BASE.glob("*.json"):
        node_name = file.name.split(".")[0]
        if node_name not in schemas:
            raise AssertionError(f"Schema found for unknown node: {file.name}.")
