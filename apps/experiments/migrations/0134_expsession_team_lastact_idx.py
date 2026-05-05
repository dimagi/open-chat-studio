from django.contrib.postgres.operations import (
    AddIndexConcurrently,
    RemoveIndexConcurrently,  # ty: ignore[unresolved-import]
)
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
        # The two indexes below are shadowed by the OneToOne unique on chat_id
        # (chat alone uniquely identifies the row, so adding team or ended_at
        # as trailing columns adds no query power) and were removed via the
        # same RemoveIndexConcurrently as part of this perf pass.
        RemoveIndexConcurrently(
            model_name="experimentsession",
            name="experiments_chat_id_d99242_idx",
        ),
        RemoveIndexConcurrently(
            model_name="experimentsession",
            name="experiments_chat_id_6337a3_idx",
        ),
    ]
