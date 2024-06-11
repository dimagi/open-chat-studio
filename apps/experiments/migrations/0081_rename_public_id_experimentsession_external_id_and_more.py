# Generated by Django 4.2.11 on 2024-06-09 10:04

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0080_remove_experiment_tools_enabled_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="experimentsession",
            old_name="public_id",
            new_name="external_id",
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="external_id",
            field=models.CharField(default=uuid.uuid4, max_length=255, unique=True),
        ),
    ]