from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """Drop the column 0007 removed from state and create the constraint that replaces
    assistant_or_node_required.

    Dropping the column takes that check and the partial unique index with it, so neither needs
    removing by name. node_required is database-only here because 0007 already added it to state.
    """

    dependencies = [
        ("custom_actions", "0007_remove_operation_assistant_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE custom_actions_customactionoperation DROP COLUMN IF EXISTS assistant_id;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.AddConstraint(
                    model_name="customactionoperation",
                    constraint=models.CheckConstraint(condition=Q(("node__isnull", False)), name="node_required"),
                ),
            ],
        ),
    ]
