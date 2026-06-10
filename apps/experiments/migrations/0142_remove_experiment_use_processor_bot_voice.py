from django.db import migrations


class Migration(migrations.Migration):
    """State-only removal of the unused Experiment.use_processor_bot_voice field.

    The DB column is kept in place so the previous code revision can keep
    running during the rolling deploy. The column is dropped in a follow-up
    migration. Adds a DB-level default so INSERTs from the new code (which no
    longer references the column) satisfy the existing NOT NULL constraint.
    """

    dependencies = [
        ("experiments", "0141_experimentsession_session_token_required"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment ALTER COLUMN use_processor_bot_voice SET DEFAULT false;",
            reverse_sql="ALTER TABLE experiments_experiment ALTER COLUMN use_processor_bot_voice DROP DEFAULT;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="experiment",
                    name="use_processor_bot_voice",
                ),
            ],
            database_operations=[],
        ),
    ]
