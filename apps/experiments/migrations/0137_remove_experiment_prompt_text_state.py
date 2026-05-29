from django.db import migrations


class Migration(migrations.Migration):
    """Remove prompt_text from Django state while leaving the DB column intact.

    The column will be dropped in a follow-up migration once this change has been
    deployed everywhere (rolling-deploy safe: old workers can still SELECT the column).
    """

    dependencies = [
        ("experiments", "0136_remove_experiment_temperature_and_more"),
    ]

    operations = [
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
