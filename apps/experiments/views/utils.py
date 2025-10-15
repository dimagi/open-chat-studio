from apps.channels.models import ChannelPlatform, ExperimentChannel


def get_channels_context(experiment) -> tuple[list[ExperimentChannel], dict[ChannelPlatform, bool]]:
    channels = experiment.experimentchannel_set.exclude(platform__in=ChannelPlatform.team_global_platforms()).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    return channels, available_platforms
