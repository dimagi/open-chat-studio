from django.db import migrations


class Migration(migrations.Migration):
    """Drop the column 0031 removed from state. Its FK constraint goes with it."""

    dependencies = [
        ("pipelines", "0033_node_position_not_null"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE pipelines_node DROP COLUMN IF EXISTS assistant_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
