import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0027_chatmessage_external_ids_idx"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatmessage",
            name="external_ids",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=600),
                blank=True,
                default=list,
                help_text="Provider message IDs this message was built from, namespaced by platform.",
                null=True,
                size=None,
            ),
        ),
    ]
