from django.contrib.postgres.operations import (
    AddIndexConcurrently,
    RemoveIndexConcurrently,  # ty: ignore[unresolved-import]
)
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0031_chatmessage_moderation_idx"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="chatmessage",
            index=models.Index(
                condition=models.Q(("moderation_status__isnull", False)),
                fields=["moderation_status", "created_at"],
                name="chatmessage_mod_created_idx",
            ),
        ),
        RemoveIndexConcurrently(
            model_name="chatmessage",
            name="chatmessage_moderation_idx",
        ),
    ]
