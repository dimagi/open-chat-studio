# Generated by Django 4.2.11 on 2024-05-27 09:13

from django.db import migrations, models
import django.db.models.deletion


def _add_experiments(apps, schema_editor):
    ScheduledMessage = apps.get_model("events", "ScheduledMessage")
    
    messages = ScheduledMessage.objects.all().annotate(source_experiment_id=models.F("action__static_trigger__experiment"))
    for message in messages:
        message.experiment_id = message.source_experiment_id
    
    ScheduledMessage.objects.bulk_update(messages, fields=["experiment"])


def _remove_experiments(apps, schema_editor):
    ScheduledMessage = apps.get_model("events", "ScheduledMessage")
    ScheduledMessage.objects.all().update(experiment=None)

class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0079_alter_participant_unique_together_and_more'),
        ('events', '0008_scheduledmessage_custom_schedule_params'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduledmessage',
            name='experiment',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_messages', to='experiments.experiment'),
        ),
        migrations.RunPython(_add_experiments, _remove_experiments),
        migrations.AlterField(
            model_name='scheduledmessage',
            name='experiment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_messages', to='experiments.experiment'),
        ),
    ]
