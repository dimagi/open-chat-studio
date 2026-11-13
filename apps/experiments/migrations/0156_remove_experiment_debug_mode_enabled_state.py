from django.db import migrations


class Migration(migrations.Migration):
    """State-only removal of Experiment.debug_mode_enabled.

    The DB column stays so the previous code revision keeps running during the
    deploy, and is dropped in a follow-up migration. The DB-level default lets
    INSERTs from the new code, which no longer sets the column, satisfy its
    NOT NULL constraint.
    """

    dependencies = [
        ("experiments", "0155_experiment_unavailable_message"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE experiments_experiment ALTER COLUMN debug_mode_enabled SET DEFAULT false;",
            reverse_sql="ALTER TABLE experiments_experiment ALTER COLUMN debug_mode_enabled DROP DEFAULT;",
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="experiment",
                    name="debug_mode_enabled",
                ),
            ],
            database_operations=[],
        ),
    ]
