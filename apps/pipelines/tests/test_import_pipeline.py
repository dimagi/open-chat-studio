import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

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
    assert "nodes" not in pipeline.data
    names = {node.params["name"] for node in pipeline.node_set.all()}
    assert names == {"start", "end"}


@pytest.mark.django_db()
def test_import_preserves_unknown_top_level_keys(tmp_path):
    """Requiring ``nodes`` must not also discard the file's viewport: dropping unknown keys
    is an HTTP-input rule, not a property of a complete graph."""
    team = TeamFactory()
    file_path = tmp_path / "pipeline.json"
    file_path.write_text(json.dumps({**_old_format_flow(), "viewport": {"x": 9, "y": 9, "zoom": 2}}))

    call_command("import_pipeline", team.slug, "Imported", str(file_path))

    pipeline = Pipeline.objects.get(team=team, name="Imported")
    assert pipeline.data["viewport"] == {"x": 9, "y": 9, "zoom": 2}


@pytest.mark.django_db()
def test_import_file_without_nodes_key_is_rejected(tmp_path):
    """A file with no ``nodes`` is malformed, not an empty graph: importing it as empty
    would silently create a pipeline with no nodes."""
    team = TeamFactory()
    file_path = tmp_path / "pipeline.json"
    file_path.write_text(json.dumps({"edges": []}))

    with pytest.raises(CommandError, match="Invalid pipeline data"):
        call_command("import_pipeline", team.slug, "Imported", str(file_path))

    assert not Pipeline.objects.filter(team=team).exists()
