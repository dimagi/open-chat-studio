from django.db import migrations


class Migration(migrations.Migration):
    """Drop the column 0031 removed from state. Its FK constraint goes with it."""

    dependencies = [
        ("pipelines", "0031_remove_node_assistant_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE pipelines_node DROP COLUMN IF EXISTS assistant_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
