# Generated by Django 4.2 on 2023-10-12 11:02

import django.db.models.deletion
from django.db import migrations, models

from apps.utils.teams_migration import assign_model_to_team_migration


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0039_populate_team"),
    ]

    operations = [
        migrations.RunPython(
            assign_model_to_team_migration("experiments.ConsentForm", delete_if_no_team=True),
            migrations.RunPython.noop, elidable=True
        ),
        migrations.RunPython(assign_model_to_team_migration("experiments.Experiment"), migrations.RunPython.noop, elidable=True),
        migrations.RunPython(
            assign_model_to_team_migration("experiments.NoActivityMessageConfig"), migrations.RunPython.noop, elidable=True
        ),
        migrations.RunPython(assign_model_to_team_migration("experiments.Prompt"), migrations.RunPython.noop, elidable=True),
        migrations.RunPython(
            assign_model_to_team_migration("experiments.PromptBuilderHistory"), migrations.RunPython.noop, elidable=True
        ),
        migrations.RunPython(assign_model_to_team_migration("experiments.SafetyLayer"), migrations.RunPython.noop, elidable=True),
        migrations.RunPython(assign_model_to_team_migration("experiments.SourceMaterial"), migrations.RunPython.noop, elidable=True),
        migrations.RunPython(
            assign_model_to_team_migration("experiments.Survey"), migrations.RunPython.noop, elidable=True
        ),
        migrations.RunPython(
            assign_model_to_team_migration("experiments.ExperimentSession"), migrations.RunPython.noop, elidable=True
        ),
        migrations.AlterField(
            model_name="consentform",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="experiment",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="noactivitymessageconfig",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="prompt",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="promptbuilderhistory",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="safetylayer",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="sourcematerial",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
        migrations.AlterField(
            model_name="survey",
            name="team",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="teams.team", verbose_name="Team"),
        ),
    ]
