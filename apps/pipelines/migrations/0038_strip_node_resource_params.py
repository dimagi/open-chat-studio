from django.db import migrations

from apps.pipelines.migrations.utils.strip_node_resource_params import (
    restore_node_resource_params,
    strip_node_resource_params,
)


class Migration(migrations.Migration):
    """Make Node relations authoritative after the relation-aware write path is deployed."""

    atomic = False

    dependencies = [
        ("pipelines", "0037_merge_20261105_0904"),
        ("custom_actions", "0008_drop_operation_assistant_column"),
        ("documents", "0022_alter_documentsource_source_type"),
        ("experiments", "0156_remove_experiment_debug_mode_enabled_state"),
        ("service_providers", "0104_voiceprovider_gemini"),
    ]

    operations = [
        migrations.RunPython(strip_node_resource_params, restore_node_resource_params),
    ]
