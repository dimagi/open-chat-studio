# Generated by Django 4.2.15 on 2024-08-16 08:25

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0006_remove_invitation_role_remove_membership_role"),
        ("channels", "0018_alter_experimentchannel_platform"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentchannel",
            name="team",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="teams.team",
                verbose_name="Team",
                null=True,
                blank=True,
            ),
        ),
        # precautionary cleanup (spot checks show no nulls)
        migrations.RunSQL("DELETE from channels_experimentchannel WHERE experiment_id IS NULL"),
        migrations.RunSQL(
            """
            UPDATE channels_experimentchannel ec set team_id = e.team_id
            FROM experiments_experiment e
            WHERE e.id = ec.experiment_id;
            """,
            migrations.RunSQL.noop,
            elidable=True
        ),
        # remove null=True, blank=True from team field
        migrations.AlterField(
            model_name="experimentchannel",
            name="team",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="teams.team",
                verbose_name="Team",
            ),
        ),
    ]