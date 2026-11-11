from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


def _trigram_index(column):
    name = f"participant_{column}_upper_trgm_idx"
    return migrations.RunSQL(
        sql=f'CREATE INDEX CONCURRENTLY "{name}" ON "experiments_participant" '
        f'USING gin ((UPPER("{column}")) gin_trgm_ops)',
        reverse_sql=f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"',
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("experiments", "0154_participant_strikes"),
        ("web", "0002_auto_20241129_1504"),
    ]

    operations = [
        _trigram_index("identifier"),
        _trigram_index("name"),
        AddIndexConcurrently(
            model_name="participant",
            index=models.Index(fields=["remote_id"], name="participant_remote_id_idx"),
        ),
    ]
