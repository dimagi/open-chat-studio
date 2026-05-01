from django.contrib.postgres.operations import RemoveIndexConcurrently  # ty: ignore[unresolved-import]
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("experiments", "0134_expsession_team_lastact_idx"),
    ]

    operations = [
        RemoveIndexConcurrently(
            model_name="experimentsession",
            name="experiments_chat_id_d99242_idx",
        ),
        RemoveIndexConcurrently(
            model_name="experimentsession",
            name="experiments_chat_id_6337a3_idx",
        ),
    ]
