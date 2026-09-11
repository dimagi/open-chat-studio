from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """Remove the assistant field and its constraints from Django state only.

    The database keeps both constraints until phase 2b and they stay satisfiable: new rows set
    node, and the unique constraint is conditional on a non-null assistant. node_required is
    created for real alongside the column drop.
    """

    dependencies = [
        ("assistants", "0016_delete_assistant_data"),
        ("custom_actions", "0006_remove_customactionoperation_experiment_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="customactionoperation", name="unique_assistant_custom_action_operation"
                ),
                migrations.RemoveConstraint(model_name="customactionoperation", name="assistant_or_node_required"),
                migrations.RemoveField(model_name="customactionoperation", name="assistant"),
                migrations.AddConstraint(
                    model_name="customactionoperation",
                    constraint=models.CheckConstraint(condition=Q(("node__isnull", False)), name="node_required"),
                ),
            ],
            database_operations=[],
        ),
    ]
