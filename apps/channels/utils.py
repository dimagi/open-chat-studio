from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment


def validate_platform_availability(experiment: Experiment, platform: ChannelPlatform):
    channels = experiment.experimentchannel_set.exclude(
        platform__in=[ChannelPlatform.WEB, ChannelPlatform.API, ChannelPlatform.EVALUATIONS]
    ).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)

    if not available_platforms.get(platform):
        raise ExperimentChannelException("Platform not found or not available in this team.")

    existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
    if platform in existing_platforms:
        raise ExperimentChannelException(f"Channel for {platform.label} already exists")
