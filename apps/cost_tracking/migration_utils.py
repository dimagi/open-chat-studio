"""Shared helpers for cost_tracking data migrations."""

from django.core.management import call_command
from django.db import migrations


def load_pricing_data() -> migrations.RunPython:
    """Return a RunPython op that loads the seed JSON via `load_ai_pricing`.

    Use this in any data migration that needs the current seed applied:

        operations = [
            load_pricing_data(),
        ]
    """

    def forwards(apps, schema_editor):
        call_command("load_ai_pricing", verbosity=0)

    return migrations.RunPython(forwards, migrations.RunPython.noop)
