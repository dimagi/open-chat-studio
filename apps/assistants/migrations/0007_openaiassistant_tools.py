# Generated by Django 4.2.14 on 2024-08-07 15:00

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assistants', '0006_openaiassistant_include_file_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='openaiassistant',
            name='tools',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=128), blank=True, default=list, size=None),
        ),
    ]
