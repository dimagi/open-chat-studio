from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_alter_customuser_managers"),
    ]

    operations = [
        RunDataMigration("migrate_2fa_data")
    ]
