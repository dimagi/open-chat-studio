from django.contrib.postgres.operations import (
    AddIndexConcurrently,  # ty: ignore[unresolved-import]
)
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bot_channels", "0028_notify_widget_version_release_0_10_0"),
        ("chat", "0025_chatmessage_chatmessage_created_at_idx"),
        ("experiments", "0145_experimentsession_expsession_created_at_idx_and_more"),
        ("teams", "0014_team_is_migrating"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="experimentsession",
            index=models.Index(
                fields=["team", "first_activity_at"],
                name="expsession_team_firstact_idx",
            ),
        ),
    ]
