from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0156_remove_experiment_debug_mode_enabled_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS debug_mode_enabled;",
            reverse_sql=(
                "ALTER TABLE experiments_experiment "
                "ADD COLUMN IF NOT EXISTS debug_mode_enabled boolean NOT NULL DEFAULT false;"
            ),
        ),
    ]
