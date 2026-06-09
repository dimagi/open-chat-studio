"""Collect the channels to show in the inspect response.

Channels are always attached to the working version, so we look them up there no matter which
version is being inspected. The team-wide web and API channels are fetched read-only on purpose:
the manager's ``get_team_*_channel`` helpers use ``get_or_create``, and inspect is a GET request,
so creating rows here would be an unwanted side effect.
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
        channel = (
            ExperimentChannel.objects.filter(team=team, platform=platform, name=name)
            .select_related("messaging_provider")
            .first()
        )
        if channel is not None:
            channels.append(channel)
    return channels
