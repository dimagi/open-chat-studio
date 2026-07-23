from django.db import migrations

from apps.pipelines.migrations.utils.strip_node_data import (
    rebuild_node_data_in_pipelines,
    strip_node_data_from_pipelines,
)


def _strip_node_data(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Node = apps.get_model("pipelines", "Node")
    strip_node_data_from_pipelines(Pipeline, Node)


def _rebuild_node_data(apps, schema_editor):
    # Pre-ADR-0046 code requires the embedded blob, so reversing rebuilds it from the
    # Node rows (which own the content and were untouched by the forward migration).
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Node = apps.get_model("pipelines", "Node")
    rebuild_node_data_in_pipelines(Pipeline, Node)


class Migration(migrations.Migration):
    # The batched strip/rebuild is idempotent and rerunnable; committing incrementally
    # avoids holding row locks on the whole Pipeline table in one transaction.
    atomic = False

    dependencies = [
        ("pipelines", "0028_pipeline_edit_revision"),
    ]

    operations = [
        migrations.RunPython(_strip_node_data, _rebuild_node_data, elidable=True),
    ]
