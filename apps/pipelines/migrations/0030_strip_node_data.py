from django.core.management import call_command
from django.db import migrations

from apps.pipelines.migrations.utils.strip_node_data import rebuild_node_data_in_pipelines


def strip_node_data(apps, schema_editor):
    """Backfill Node.position_x/position_y from the blob, then drop Pipeline.data["nodes"].

    Must run before phase-2 code serves reads: an un-backfilled node reads back without a
    position, and the next save then destroys the blob holding the only copy of that layout.

    ``strip_node_data`` is an IdempotentCommand, so it records its own run-once marker and a
    later manual rerun needs ``--force``. Not RunDataMigration, which is irreversible and
    would make the reverse below unreachable.
    """
    call_command("strip_node_data")


def rebuild_node_data(apps, schema_editor):
    """Reverse: rebuild the nodes list (with embedded content blobs) from the Node rows.

    Pre-ADR-0048 code requires ``Pipeline.data["nodes"]``, so a code rollback needs it back.
    The rows own both content and layout and are untouched by the strip, which makes them a
    complete source. Historical models — the helper takes them as arguments for this reason.
    """
    rebuild_node_data_in_pipelines(apps.get_model("pipelines", "Pipeline"), apps.get_model("pipelines", "Node"))


class Migration(migrations.Migration):
    # Non-atomic so the helper's batches commit independently (the command sets atomic = False
    # for the same reason). An atomic migration would wrap the whole strip in one transaction.
    atomic = False

    dependencies = [
        ("pipelines", "0029_node_position_x_node_position_y"),
        # The command records its run-once marker in data_migrations.CustomMigration.
        ("data_migrations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(strip_node_data, rebuild_node_data),
    ]
