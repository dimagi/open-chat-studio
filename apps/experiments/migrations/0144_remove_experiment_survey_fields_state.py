from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove pre_survey/post_survey from Django state, leaving the columns in
    place (dropped in Phase 2, mirroring 0139 -> 0140). Also drops the
    'pending-pre-survey' choice from ExperimentSession.status (no DB change).
    """

    dependencies = [
        ("experiments", "0143_null_experiment_surveys"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="experiment", name="pre_survey"),
                migrations.RemoveField(model_name="experiment", name="post_survey"),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="status",
            field=models.CharField(
                choices=[
                    ("setup", "Setting Up"),
                    ("pending", "Awaiting participant"),
                    ("active", "Active"),
                    ("pending-review", "Awaiting final review."),
                    ("complete", "Complete"),
                    ("unknown", "Unknown"),
                ],
                default="setup",
                max_length=20,
            ),
        ),
    ]
