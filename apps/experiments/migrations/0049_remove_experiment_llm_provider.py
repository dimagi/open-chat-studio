# Generated by Django 4.2 on 2023-10-26 10:16

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0048_experiment_llm_provider_new"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="experiment",
            name="llm_provider",
        ),
    ]
