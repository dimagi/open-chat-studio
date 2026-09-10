"""Delete the `flag_events` row now that events are on for every team."""

from django.db import migrations

from apps.teams.migration_utils import delete_waffle_flag


def delete_events_flag(apps, schema_editor):
    delete_waffle_flag(flag_model=apps.get_model("teams", "Flag"), flag_name="flag_events")


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0017_team_require_mfa"),
    ]

    operations = [
        migrations.RunPython(delete_events_flag, migrations.RunPython.noop),
    ]
