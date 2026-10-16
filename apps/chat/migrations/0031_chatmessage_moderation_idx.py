from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0030_chatmessage_moderation_columns"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="chatmessage",
            index=models.Index(fields=["moderation_status"], name="chatmessage_moderation_idx"),
        ),
    ]
