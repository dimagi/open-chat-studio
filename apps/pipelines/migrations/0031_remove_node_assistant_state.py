from django.db import migrations


class Migration(migrations.Migration):
    """Remove Node.assistant from Django state, leaving the column for the phase-2b drop."""

    dependencies = [
        ("assistants", "0016_delete_assistant_data"),
        ("pipelines", "0030_strip_node_data"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="node", name="assistant"),
            ],
            database_operations=[],
        ),
    ]
