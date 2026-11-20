from django.db import migrations, models


def create_cutover_state(apps, schema_editor):
    """Create the singleton compatibility state used by rolling deployments."""
    state_model = apps.get_model("pipelines", "NodeResourceCutoverState")
    state_model.objects.using(schema_editor.connection.alias).get_or_create(pk=1)


def delete_cutover_state(apps, schema_editor):
    """Remove the singleton state when reversing this additive schema migration."""
    state_model = apps.get_model("pipelines", "NodeResourceCutoverState")
    state_model.objects.using(schema_editor.connection.alias).filter(pk=1).delete()


class Migration(migrations.Migration):
    """Add the rollout state without changing existing node data."""

    dependencies = [
        ("pipelines", "0037_merge_20261105_0904"),
    ]

    operations = [
        migrations.CreateModel(
            name="NodeResourceCutoverState",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("legacy_json_compatibility_enabled", models.BooleanField(default=False)),
                ("cleanup_completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "pipelines_node_resource_cutover_state"},
        ),
        migrations.RunPython(create_cutover_state, delete_cutover_state),
    ]
