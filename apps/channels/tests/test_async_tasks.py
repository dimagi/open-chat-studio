import pytest

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment
from apps.users.models import CustomUser


@pytest.mark.asyncio()
class TestAsyncInfrastructure:
    """Tests to validate async testing infrastructure."""

    async def test_django_async_orm(self, db, async_team_with_users):
        """Test that Django async ORM methods work."""
        team = async_team_with_users

        # Test afirst to get a user
        user = await CustomUser.objects.filter(teams=team).afirst()
        assert user is not None

        # Test aget
        user_again = await CustomUser.objects.aget(email=user.email)
        assert user_again.id == user.id

        # Test aget_or_create
        channel, created = await ExperimentChannel.objects.aget_or_create(
            team=team, platform=ChannelPlatform.API, name=f"{team.slug}-test-channel"
        )
        assert channel is not None

        # Test acount
        count = await ExperimentChannel.objects.filter(team=team).acount()
        assert count == 1

    async def test_async_fixture_usage(self, async_experiment_with_pipeline):
        """Test that async fixtures work correctly."""
        experiment = async_experiment_with_pipeline
        assert experiment is not None
        assert experiment.team is not None
        assert experiment.pipeline is not None

        # Verify we can do async queries
        reloaded = await Experiment.objects.aget(id=experiment.id)
        assert reloaded.id == experiment.id
