from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.pipelines.utils import compute_max_char_limit


def get_channels_context(experiment) -> tuple[list[ExperimentChannel], dict[ChannelPlatform, bool]]:
    channels = experiment.experimentchannel_set.exclude(platform__in=ChannelPlatform.team_global_platforms()).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    return channels, available_platforms


def get_max_char_limit(experiment_version) -> int | None:
    pipeline = experiment_version.pipeline
    if not pipeline:
        return None
    return compute_max_char_limit(pipeline)
