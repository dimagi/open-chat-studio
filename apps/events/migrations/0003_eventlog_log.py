# Generated by Django 4.2.7 on 2024-04-01 15:05

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventlog",
            name="log",
            field=models.TextField(blank=True),
        ),
    ]
