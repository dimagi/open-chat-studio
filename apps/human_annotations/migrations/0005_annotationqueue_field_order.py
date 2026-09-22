import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("human_annotations", "0004_backfill_authoritative"),
    ]

    operations = [
        migrations.AddField(
            model_name="annotationqueue",
            name="field_order",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.TextField(),
                blank=True,
                default=list,
                help_text="Field names in display order; names not listed fall back to schema order",
                null=True,
                size=None,
            ),
        ),
    ]
