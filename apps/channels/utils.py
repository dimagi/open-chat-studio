from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment


def validate_platform_availability(experiment: Experiment, platform: ChannelPlatform):
    existing_platforms = {channel.platform_enum for channel in experiment.experimentchannel_set.all()}
    if platform in existing_platforms:
        raise ExperimentChannelException(f"Channel for platform '{platform.label}' already exists")

    global_platforms = ChannelPlatform.team_global_platforms()
    used_platforms = {platform for platform in existing_platforms if platform not in global_platforms}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    if not available_platforms.get(platform):
        raise ExperimentChannelException("Platform already used or not available.")
