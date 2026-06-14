"""Pre-create `flag_ai_cost_monitoring` (disabled by default) so it's visible
in the admin UI before any code path checks it. WAFFLE_CREATE_MISSING_FLAGS
would otherwise create it lazily on first check, but pre-creating makes the
opt-in workflow more predictable.
"""

from django.db import migrations

FLAG_NAME = "flag_ai_cost_monitoring"


def forwards(apps, schema_editor):
    Flag = apps.get_model("teams", "Flag")
    Flag.objects.get_or_create(name=FLAG_NAME, defaults={"everyone": False})


def backwards(apps, schema_editor):
    Flag = apps.get_model("teams", "Flag")
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0009_merge_pipeline_admin_into_experiment_admin"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
