# Generated by Django 5.1 on 2024-09-10 13:57

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "experiments",
            "0092_experiment_is_archived_experiment_is_default_version_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="name",
            field=models.CharField(blank=True, max_length=320),
        ),
    ]