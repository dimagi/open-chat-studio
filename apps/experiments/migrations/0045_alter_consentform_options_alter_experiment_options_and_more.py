# Generated by Django 4.2 on 2023-10-20 14:28

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0044_load_azure_synthetic_voices"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="consentform",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="experiment",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="noactivitymessageconfig",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="participant",
            options={"ordering": ["email"]},
        ),
        migrations.AlterModelOptions(
            name="prompt",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="sourcematerial",
            options={"ordering": ["topic"]},
        ),
        migrations.AlterModelOptions(
            name="survey",
            options={"ordering": ["name"]},
        ),
        migrations.AlterModelOptions(
            name="syntheticvoice",
            options={"ordering": ["name"]},
        ),
        migrations.AlterField(
            model_name="consentform",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="experiment",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="noactivitymessageconfig",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="participant",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="prompt",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="promptbuilderhistory",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="safetylayer",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="sourcematerial",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="survey",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
        migrations.AlterField(
            model_name="syntheticvoice",
            name="id",
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
        ),
    ]
