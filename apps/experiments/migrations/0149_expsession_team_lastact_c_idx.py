from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import functions


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("experiments", "0148_alter_syntheticvoice_service"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="experimentsession",
            index=models.Index(
                models.F("team"),
                functions.Coalesce("last_activity_at", "created_at").desc(),
                name="expsession_team_lastact_c_idx",
            ),
        ),
    ]
