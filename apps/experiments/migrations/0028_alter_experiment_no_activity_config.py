# Generated by Django 4.2 on 2023-08-14 19:32

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0027_create_web_channels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="experiment",
            name="no_activity_config",
            field=models.ForeignKey(
                blank=True,
                help_text="This is an experimental feature and might exhibit undesirable behaviour for external channels",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="experiments.noactivitymessageconfig",
            ),
        ),
    ]
