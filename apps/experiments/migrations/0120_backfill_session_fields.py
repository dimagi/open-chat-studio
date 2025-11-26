from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0119_experimentsession_experiment_versions_and_more'),
    ]

    operations = [
        RunDataMigration("backfill_session_fields"),
    ]
