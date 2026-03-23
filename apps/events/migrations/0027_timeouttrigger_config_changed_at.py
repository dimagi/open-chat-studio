from django.db import migrations, models


def backfill_config_changed_at(apps, schema_editor):
    TimeoutTrigger = apps.get_model("events", "TimeoutTrigger")
    TimeoutTrigger.objects.filter(config_changed_at__isnull=True).update(config_changed_at=models.F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0026_timeouttrigger_trigger_from_first_message_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeouttrigger",
            name="config_changed_at",
            field=models.DateTimeField(
                help_text=(
                    "Tracks when trigger config last changed. Prevents retroactive application to old sessions."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(backfill_config_changed_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="timeouttrigger",
            name="config_changed_at",
            field=models.DateTimeField(
                help_text=(
                    "Tracks when trigger config last changed. Prevents retroactive application to old sessions."
                ),
            ),
        ),
    ]
