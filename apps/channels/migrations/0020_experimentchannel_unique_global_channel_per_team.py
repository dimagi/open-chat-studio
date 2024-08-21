# Generated by Django 4.2.15 on 2024-08-21 08:46

from django.db import migrations, models


def migrate_team_global_channels(apps, schema_editor):
    ExperimentChannel = apps.get_model("channels", "ExperimentChannel")
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")
    Team = apps.get_model("teams", "Team")

    for team in Team.objects.all():
        for platform in ["web", "api"]:
            sessions = ExperimentSession.objects.filter(
                team=team, experiment_channel__platform=platform
            )
            if sessions.exists():
                channel, _ = ExperimentChannel.objects.get_or_create(
                    team=team, platform=platform, name=f"{team.slug}-{platform}-channel"
                )
                sessions.update(experiment_channel=channel)
                ExperimentChannel.objects.exclude(pk=channel.pk).filter(
                    team=team, platform=platform
                ).delete()


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("channels", "0019_experimentchannel_team"),
    ]

    operations = [
        migrations.RunPython(migrate_team_global_channels, migrations.RunPython.noop, elidable=True),
        migrations.AddConstraint(
            model_name="experimentchannel",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted", False), ("platform__in", ["api", "web"])
                ),
                fields=("team", "platform"),
                name="unique_global_channel_per_team",
            ),
        ),
    ]
