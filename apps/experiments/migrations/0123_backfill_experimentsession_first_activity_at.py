from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ('experiments', '0122_experimentsession_first_activity_at'),
        ('data_migrations', '0001_initial'),
    ]

    operations = [
        RunDataMigration("backfill_experimentsession_first_activity", command_options={"force": True}),
    ]
