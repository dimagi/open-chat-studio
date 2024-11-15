# Generated by Django 5.1.2 on 2024-11-06 13:50

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0011_analysis_llm_provider_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="rungroup",
            name="analysis",
        ),
        migrations.RemoveField(
            model_name="analysisrun",
            name="group",
        ),
        migrations.RemoveField(
            model_name="analysisrun",
            name="input_resource",
        ),
        migrations.RemoveField(
            model_name="analysisrun",
            name="output_resources",
        ),
        migrations.RemoveField(
            model_name="resource",
            name="team",
        ),
        migrations.RemoveField(
            model_name="rungroup",
            name="created_by",
        ),
        migrations.RemoveField(
            model_name="rungroup",
            name="team",
        ),
        migrations.DeleteModel(
            name="Analysis",
        ),
        migrations.DeleteModel(
            name="AnalysisRun",
        ),
        migrations.DeleteModel(
            name="Resource",
        ),
        migrations.DeleteModel(
            name="RunGroup",
        ),
    ]
