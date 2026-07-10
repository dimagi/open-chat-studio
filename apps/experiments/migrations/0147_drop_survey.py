from django.db import migrations


class Migration(migrations.Migration):
    """Phase 2 of the survey removal: drop the physical DB objects left behind by Phase 1.

    0143 nulled the experiment pre/post-survey FK columns and 0144 removed the fields from
    Django state (deferring the physical column drop to here, mirroring 0139 -> 0140). The
    columns still carry FK constraints referencing experiments_survey, so they must be dropped
    before the survey table itself — otherwise DeleteModel's DROP TABLE hits the constraint.
    """

    dependencies = [
        ("experiments", "0146_expsession_team_firstact_idx"),
    ]

    operations = [
        migrations.RunSQL(
            # Dropping the columns implicitly drops their FK constraints to experiments_survey.
            sql=[
                "ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS pre_survey_id;",
                "ALTER TABLE experiments_experiment DROP COLUMN IF EXISTS post_survey_id;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.DeleteModel(
            name="Survey",
        ),
    ]
