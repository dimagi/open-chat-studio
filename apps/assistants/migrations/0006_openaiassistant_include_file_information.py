# Generated by Django 4.2.15 on 2024-08-19 12:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assistants', '0005_migrate_assistants'),
    ]

    operations = [
        migrations.AddField(
            model_name='openaiassistant',
            name='include_file_info',
            field=models.BooleanField(default=False),
        ),
    ]