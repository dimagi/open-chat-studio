# Generated by Django 4.2.7 on 2024-04-26 13:16

from django.db import migrations

from apps.assistants.migrations.utils import migrate_assistant_to_v2


def do_migration(apps, schema_editor):
    OpenAiAssistant = apps.get_model("assistants.OpenAiAssistant")
    for assistant in OpenAiAssistant.objects.all():
        migrate_assistant_to_v2(assistant, apps=apps)


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0001_initial"),
        ("assistants", "0004_openaiassistant_temperature_openaiassistant_top_p_and_more"),
    ]

    operations = [
        migrations.RunPython(do_migration, reverse_code=migrations.RunPython.noop),
    ]
