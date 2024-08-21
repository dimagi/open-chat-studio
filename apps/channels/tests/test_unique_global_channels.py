import pytest
from django.db import IntegrityError

from apps.channels.models import ChannelPlatform, ExperimentChannel


@pytest.mark.django_db()
@pytest.mark.parametrize("platform", ChannelPlatform.team_global_platforms())
def test_unique_global_channels(platform, team):
    ExperimentChannel.objects.create(team=team, platform=platform, name="channel1")

    with pytest.raises(IntegrityError):
        ExperimentChannel.objects.create(team=team, platform=platform, name="channel2")
