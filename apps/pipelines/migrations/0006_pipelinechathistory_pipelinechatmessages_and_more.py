# Generated by Django 5.1 on 2024-10-10 12:26

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0095_experiment_debug_mode_enabled'),
        ('pipelines', '0005_auto_20240802_0039'),
    ]

    operations = [
        migrations.CreateModel(
            name='PipelineChatHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('type', models.CharField(choices=[('node', 'Node History'), ('named', 'Named History'), ('global', 'Global History'), ('none', 'No History')], max_length=10)),
                ('name', models.CharField(db_index=True, max_length=128)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pipeline_chat_history', to='experiments.experimentsession')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PipelineChatMessages',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('human_message', models.TextField()),
                ('ai_message', models.TextField()),
                ('chat_history', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='pipelines.pipelinechathistory')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='pipelinechathistory',
            constraint=models.UniqueConstraint(fields=('session', 'type', 'name'), name='unique_session_type_name'),
        ),
    ]
