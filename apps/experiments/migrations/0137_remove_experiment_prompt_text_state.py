from django.db import migrations


class Migration(migrations.Migration):
    """Remove prompt_text from Django state while leaving the DB column intact.

    Adds a DB-level default ('') so INSERTs from the new code (which no longer
    references this column) satisfy the existing NOT NULL constraint.
    The column is dropped in a follow-up migration once deployed everywhere.
    """

    dependencies = [
        ("experiments", "0136_remove_experiment_temperature_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=["ALTER TABLE experiments_experiment ALTER COLUMN prompt_text SET DEFAULT '';"],
            reverse_sql=["ALTER TABLE experiments_experiment ALTER COLUMN prompt_text DROP DEFAULT;"],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="experiment",
                    name="prompt_text",
                ),
            ],
            database_operations=[],
        ),
    ]
