"""Tests for the inspect channel-collection helper."""

import pytest

from apps.api.v2.inspect.channels import get_channels
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_get_channels_includes_working_version_channels_and_team_web_api():
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    ExperimentChannelFactory.create(team=team, experiment=experiment, name="Support TG")
    ExperimentChannel.objects.get_team_web_channel(team)
    ExperimentChannel.objects.get_team_api_channel(team)

    platforms = [c.platform for c in get_channels(experiment)]

    assert platforms == [ChannelPlatform.TELEGRAM, ChannelPlatform.WEB, ChannelPlatform.API]


@pytest.mark.django_db()
def test_get_channels_does_not_create_team_channels():
    """A team that has never used its web/api channels gets them left out, not created on the fly."""
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    ExperimentChannelFactory.create(team=team, experiment=experiment, name="Support TG")

    platforms = [c.platform for c in get_channels(experiment)]

    assert platforms == [ChannelPlatform.TELEGRAM]
    assert not ExperimentChannel.objects.filter(
        team=team, platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]
    ).exists()
