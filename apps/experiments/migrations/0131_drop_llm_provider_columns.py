from django.db import migrations


class Migration(migrations.Migration):
    """Drop llm_provider and llm_provider_model columns from experiments_experiment.

    Migration 0130 removed these fields from Django's state using SeparateDatabaseAndState
    but left the actual DB columns and FK constraints in place. This migration completes
    the removal by dropping the columns from the database.
    """

    dependencies = [
        ("experiments", "0130_remove_experiment_llm_provider_and_more"),
        ("service_providers", "0043_migrate_gemini_3_pro_preview"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS llm_provider_model_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS llm_provider_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
