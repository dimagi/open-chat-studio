from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0027_backfill_node_fks"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipeline",
            name="edit_revision",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
