from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bot_channels", "0025_alter_experimentchannel_platform"),
        ("chat", "0024_delete_orphaned_chats"),
        ("experiments", "0133_alter_syntheticvoice_service"),
        ("teams", "0009_merge_pipeline_admin_into_experiment_admin"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="experimentsession",
            index=models.Index(
                fields=["team", "-last_activity_at"],
                name="expsession_team_lastact_idx",
            ),
        ),
    ]
