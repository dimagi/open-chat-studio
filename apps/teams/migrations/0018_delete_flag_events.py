"""Delete the `flag_events` row now that events are on for every team.

The historical model has no `flush()`, so the cache is cleared through the concrete one.
"""

from django.db import migrations
from waffle import get_waffle_flag_model

FLAG_NAME = "flag_events"


def delete_events_flag(apps, schema_editor):
    Flag = apps.get_model("teams", "Flag")
    Flag.objects.filter(name=FLAG_NAME).delete()
    get_waffle_flag_model()(name=FLAG_NAME).flush()


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0017_team_require_mfa"),
    ]

    operations = [
        migrations.RunPython(delete_events_flag, migrations.RunPython.noop),
    ]
