"""Shared helpers for tests that set up email channels."""

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.utils.factories.experiment import ExperimentFactory


def make_email_channel(team, experiment=None, email_address="bot@chat.openchatstudio.com", is_default=False):
    """Helper to create an email ExperimentChannel."""
    if experiment is None:
        experiment = ExperimentFactory(team=team)
    extra = {"email_address": email_address}
    if is_default:
        extra["is_default"] = True
    return ExperimentChannel.objects.create(
        team=team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data=extra,
        name=f"email-{email_address}",
    )
