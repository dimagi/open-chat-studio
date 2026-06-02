from django.db import migrations


class Migration(migrations.Migration):
    """Drop the DB columns left behind by the state-only field removals in #3467–#3470.

    State was removed in:
      - 0136 (temperature, tools, citations_enabled) — #3467
      - 0137 (source_material)                       — #3470
      - 0138 (input_formatter)                       — #3469
      - 0139 (prompt_text)                           — #3468

    This migration completes the job by dropping the physical columns.
    Dropping source_material_id also implicitly drops its FK constraint.
    All operations are irreversible by design — matches the llm_provider precedent in 0131.
    """

    dependencies = [
        ("experiments", "0139_remove_experiment_prompt_text_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS temperature;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS tools;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS citations_enabled;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS prompt_text;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS input_formatter;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            # Dropping the column implicitly drops the FK constraint.
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS source_material_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
