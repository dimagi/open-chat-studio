# Generated by Django 4.2.11 on 2024-07-17 09:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0014_delete_no_activity_ping_schedule_20240619_1527'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='metadata',
            field=models.JSONField(default=dict),
        ),
    ]