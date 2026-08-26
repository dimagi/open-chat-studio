import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0026_chatmessage_external_ids"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="chatmessage",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["external_ids"], name="chatmessage_external_ids_idx"
            ),
        ),
    ]
