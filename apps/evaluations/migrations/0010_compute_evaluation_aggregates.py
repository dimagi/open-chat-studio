from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0009_evaluationrunaggregate"),
    ]

    operations = [
        RunDataMigration("compute_evaluation_aggregates"),
    ]
