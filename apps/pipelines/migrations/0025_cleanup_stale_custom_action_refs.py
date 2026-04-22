from django.core.management import call_command
from django.db import migrations


def cleanup_stale_custom_action_refs(apps, schema_editor):
    call_command("cleanup_stale_custom_action_refs")


class Migration(migrations.Migration):

    dependencies = [
        ("pipelines", "0024_make_router_keywords_upper"),
        ("custom_actions", "0006_remove_customactionoperation_experiment_and_more"),
    ]

    operations = [
        migrations.RunPython(cleanup_stale_custom_action_refs, reverse_code=migrations.RunPython.noop),
    ]
