import json
import pathlib

import pytest

from apps.pipelines.nodes.node_metadata import get_node_schemas

BASE = pathlib.Path(__file__).parent / "node_schemas"


def _live_schemas():
    """Schemas for the node types the discovery API serves -- deprecated ones are never listed."""
    return [schema for schema in get_node_schemas() if not schema.get("ui:deprecated")]


@pytest.mark.parametrize("schema", _live_schemas(), ids=lambda schema: schema["title"])
def test_every_param_is_described(schema):
    """`title` is derived from the field name ("Llm Provider Model Id"), so it tells an agent
    nothing the key didn't already. The v2 discovery API serves these schemas to an LLM that has no
    UI, no tooltips and no changelog to fall back on, so every param it may write needs a
    `description` saying what the value does.
    """
    undescribed = [name for name, prop in schema["properties"].items() if not prop.get("description", "").strip()]
    assert not undescribed, (
        f"{schema['title']} params without a description: {undescribed}. "
        f"Add `description=` to the pydantic Field -- the pipeline builder shows it as help text and "
        f"/api/v2/pipeline/nodes/ serves it to agents."
    )


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
