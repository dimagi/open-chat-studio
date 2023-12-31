# Generated by Django 4.2.7 on 2023-12-19 14:16

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0006_alter_analysisrun_status_alter_rungroup_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="analysisrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("error", "Error"),
                    ("cancelling", "Cancelling"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="rungroup",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("error", "Error"),
                    ("cancelling", "Cancelling"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=128,
            ),
        ),
    ]
