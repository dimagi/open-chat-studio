"""Pre-create `flag_ignore_rate_limiting` (disabled by default) so it's visible
in the admin UI before an incident. WAFFLE_CREATE_MISSING_FLAGS would otherwise
create it lazily on first check, leaving the exemption/kill switch invisible
until then.
"""

from django.db import migrations

FLAG_NAME = "flag_ignore_rate_limiting"


def forwards(apps, schema_editor):
    Flag = apps.get_model("teams", "Flag")
    Flag.objects.get_or_create(name=FLAG_NAME, defaults={"everyone": False})


def backwards(apps, schema_editor):
    Flag = apps.get_model("teams", "Flag")
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0014_team_is_migrating"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
