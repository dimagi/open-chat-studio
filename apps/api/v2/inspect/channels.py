"""Channel collection for the inspect projection.

Channels are only ever linked to the working version, so we resolve the family head regardless of
which version is being inspected. The team-global web/API channels are looked up read-only — the
manager's ``get_team_*_channel`` helpers use ``get_or_create``, which would make this GET-only
inspect flow write rows as a side effect.
"""

from apps.channels.models import ChannelPlatform, ExperimentChannel


def get_channels(experiment) -> list[ExperimentChannel]:
    channels = list(
        ExperimentChannel.objects.filter(experiment_id=experiment.get_working_version_id()).select_related(
            "messaging_provider"
        )
    )
    team = experiment.team
    for platform, name in (
        (ChannelPlatform.WEB, f"{team.slug}-web-channel"),
        (ChannelPlatform.API, f"{team.slug}-api-channel"),
    ):
        channel = ExperimentChannel.objects.filter(team=team, platform=platform, name=name).first()
        if channel is not None:
            channels.append(channel)
    return channels
