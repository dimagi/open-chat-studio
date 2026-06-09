"""Load the in-repo LLM pricing seed via the `load_ai_pricing` command.

Idempotent: applying the migration on a database that already has the seed
rows is a no-op (the loader returns "unchanged" for matching rates).
"""

from django.core.management import call_command
from django.db import migrations


def forwards(apps, schema_editor):
    call_command("load_ai_pricing", verbosity=0)


class Migration(migrations.Migration):
    dependencies = [
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
