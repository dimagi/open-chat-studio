from django.db import migrations

from apps.pipelines.migrations.utils import backfill_router_prompt_mode as backfill


def backfill_router_prompt_mode(apps, schema_editor):
    backfill.backfill_router_prompt_mode(apps.get_model("pipelines", "Node"))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("pipelines", "0035_updated_at_id_indexes"),
    ]

    operations = [
        migrations.RunPython(backfill_router_prompt_mode, migrations.RunPython.noop),
    ]
