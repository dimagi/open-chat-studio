# Generated by Django 4.2.7 on 2024-04-30 09:13

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('teams', '0005_invitation_groups'),
        ('experiments', '0078_alter_participant_external_chat_id'),
        ('events', '0005_alter_statictrigger_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventaction',
            name='action_type',
            field=models.CharField(choices=[('log', 'Log the last message'), ('end_conversation', 'End the conversation'), ('summarize', 'Summarize the conversation'), ('send_message_to_bot', 'Prompt the bot to message the user'), ('schedule_trigger', 'Trigger a schedule')]),
        ),
        migrations.CreateModel(
            name='ScheduledMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('next_trigger_date', models.DateTimeField(null=True)),
                ('last_triggered_at', models.DateTimeField(null=True)),
                ('total_triggers', models.IntegerField(default=0)),
                ('is_complete', models.BooleanField(default=False)),
                ('action', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_messages', to='events.eventaction')),
                ('participant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schduled_messages', to='experiments.participant')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team', verbose_name='Team')),
            ],
            options={
                'indexes': [models.Index(fields=['is_complete'], name='events_sche_is_comp_88a37a_idx')],
            },
        ),
    ]
