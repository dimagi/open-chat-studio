import json

import pytest
from django.core.management import call_command

from apps.pipelines.models import Pipeline
from apps.utils.factories.team import TeamFactory


def _old_format_flow():
    """A pipeline export in the old format: node content embedded in the data."""
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"id": "start-1", "type": "StartNode", "params": {"name": "start"}},
            },
            {
                "id": "end-1",
                "type": "endNode",
                "position": {"x": 100, "y": 0},
                "data": {"id": "end-1", "type": "EndNode", "params": {"name": "end"}},
            },
        ],
        "edges": [{"id": "e1", "source": "start-1", "target": "end-1"}],
    }


@pytest.mark.django_db()
def test_import_pipeline_from_old_format_file(tmp_path):
    team = TeamFactory()
    file_path = tmp_path / "pipeline.json"
    file_path.write_text(json.dumps(_old_format_flow()))

    call_command("import_pipeline", team.slug, "Imported", str(file_path))

    pipeline = Pipeline.objects.get(team=team, name="Imported")
    assert all("data" not in node for node in pipeline.data["nodes"])
    names = {node.params["name"] for node in pipeline.node_set.all()}
    assert names == {"start", "end"}
