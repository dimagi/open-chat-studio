# Generated by Django 5.1 on 2024-10-24 00:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pipelines', '0006_pipelinechathistory_pipelinechatmessages_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipelinechatmessages',
            name='node_id',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pipelinechatmessages',
            name='summary',
            field=models.TextField(null=True),
        ),
    ]
