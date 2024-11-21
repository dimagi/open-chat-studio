import json
import pathlib

import pytest

from apps.pipelines.views import _pipeline_node_schemas

BASE = pathlib.Path(__file__).parent / "data"


def test_schemas():
    schemas = _pipeline_node_schemas()
    for schema in schemas:
        title = schema["title"]
        assert schema["description"], title
        assert schema["ui:label"], title

        path = BASE / f"{title}.json"
        assert schema == json.loads(path.read_text())


@pytest.mark.skipif(True, reason="Only used to update schemas")
def test_update_schemas():
    schemas = _pipeline_node_schemas()
    for schema in schemas:
        title = schema["title"]
        path = BASE / f"{title}.json"
        path.write_text(json.dumps(schema, indent=2))
