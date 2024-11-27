import json
import pathlib

from apps.pipelines.views import _pipeline_node_schemas

BASE = pathlib.Path(__file__).parent / "data"


def test_schemas():
    schemas = _pipeline_node_schemas()
    for schema in schemas:
        title = schema["title"]
        assert schema["description"], title
        assert schema["ui:label"], title

        path = BASE / f"{title}.json"
        if schema != json.loads(path.read_text()):
            raise AssertionError(
                f"Pipeline schema for {title} has changed. Run 'python manage.py update_pipeline_schema'."
            )
