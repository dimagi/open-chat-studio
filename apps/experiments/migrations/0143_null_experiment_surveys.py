from django.db import migrations


class Migration(migrations.Migration):
    """Null the experiment pre/post-survey FK columns and move any sessions
    stuck in 'pending-pre-survey' to 'active', ahead of removing the fields.

    Nulling is backwards-compatible: it makes still-running pre-deploy code
    behave like the new code (no pre-survey -> PENDING goes straight to ACTIVE),
    and leaves no column referencing a Survey row, so survey deletes during the
    read-only window cannot hit an FK violation.
    """

    dependencies = [
        ("experiments", "0142_remove_experiment_use_processor_bot_voice"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "UPDATE experiments_experiment SET pre_survey_id = NULL, post_survey_id = NULL "
                "WHERE pre_survey_id IS NOT NULL OR post_survey_id IS NOT NULL;",
                "UPDATE experiments_experimentsession SET status = 'active' WHERE status = 'pending-pre-survey';",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
