from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0024_delete_orphaned_chats"),
        ("experiments", "0133_alter_syntheticvoice_service"),
        ("teams", "0009_merge_pipeline_admin_into_experiment_admin"),
        ("trace", "0011_add_trace_metrics"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["team", "-timestamp"],
                name="trace_team_timestamp_idx",
                condition=~Q(status="pending"),
            ),
        ),
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["experiment", "-timestamp"],
                name="trace_experiment_timestamp_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["session", "-timestamp"],
                name="trace_session_timestamp_idx",
            ),
        ),
    ]
