import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0025_chatmessage_chatmessage_created_at_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="external_ids",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=600),
                blank=True,
                default=list,
                help_text="Provider message IDs this message was built from, namespaced by platform.",
                size=None,
            ),
        ),
    ]
