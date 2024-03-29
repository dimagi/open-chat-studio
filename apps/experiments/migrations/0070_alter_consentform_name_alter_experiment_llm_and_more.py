# Generated by Django 4.2.7 on 2024-03-11 08:06

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0069_merge_20240311_0806"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consentform",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="experiment",
            name="llm",
            field=models.CharField(
                blank=True,
                help_text="The LLM model to use.",
                max_length=255,
                verbose_name="LLM Model",
            ),
        ),
        migrations.AlterField(
            model_name="experiment",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="llm",
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name="noactivitymessageconfig",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="safetylayer",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="survey",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="syntheticvoice",
            name="name",
            field=models.CharField(
                help_text="The name of the synthetic voice, as per the documentation of the service",
                max_length=128,
            ),
        ),
    ]
