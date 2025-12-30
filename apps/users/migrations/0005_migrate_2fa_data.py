from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_alter_customuser_managers"),
        ("mfa", "0001_initial"),
    ]

    operations = [
        RunDataMigration("migrate_2fa_data")
    ]
