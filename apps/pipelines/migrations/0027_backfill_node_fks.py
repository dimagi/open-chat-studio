from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    # Non-atomic so the command's batches commit independently (it sets atomic = False for the
    # same reason). An atomic migration would wrap the whole backfill in a single transaction.
    atomic = False

    dependencies = [
        ("pipelines", "0026_node_resource_fks"),
        ("data_migrations", "0001_initial"),
    ]

    operations = [
        RunDataMigration("backfill_node_fks"),
    ]
