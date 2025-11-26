import pytest
from asgiref.sync import sync_to_async

from apps.channels.datamodels import BaseMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import ApiChannel
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant


@pytest.mark.asyncio()
class TestAsyncSessionManagement:
    """Tests for async session management."""

    async def test_load_latest_session(self, async_experiment_with_pipeline, db):
        """Test async session loading."""
        from apps.users.models import CustomUser

        experiment = async_experiment_with_pipeline
        participant_id = "test-participant"

        # Get a user from the team
        user = await CustomUser.objects.filter(teams=experiment.team).afirst()

        channel_model, _ = await ExperimentChannel.objects.aget_or_create(
            team=experiment.team, platform=ChannelPlatform.API, name=f"{experiment.team.slug}-api"
        )

        # Create channel instance (sync constructor)
        channel = await sync_to_async(ApiChannel)(experiment, channel_model, experiment_session=None, user=user)
        channel._participant_identifier = participant_id

        # Test loading (should be None initially)
        await channel._aload_latest_session()
        assert channel.experiment_session is None

        # Create a session manually
        participant, _ = await Participant.objects.aget_or_create(
            identifier=participant_id, team=experiment.team, defaults={"platform": ChannelPlatform.API}
        )

        chat = await Chat.objects.acreate(team=experiment.team)
        session = await ExperimentSession.objects.acreate(
            team=experiment.team, experiment=experiment, participant=participant, chat=chat
        )

        # Test loading again (should find the session)
        await channel._aload_latest_session()
        assert channel.experiment_session is not None
        assert channel.experiment_session.id == session.id

    async def test_ensure_session_exists(self, async_experiment_with_pipeline, db):
        """Test async session creation."""
        from apps.users.models import CustomUser

        experiment = async_experiment_with_pipeline
        participant_id = "test-participant-new"

        # Get a user from the team
        user = await CustomUser.objects.filter(teams=experiment.team).afirst()

        channel_model, _ = await ExperimentChannel.objects.aget_or_create(
            team=experiment.team, platform=ChannelPlatform.API, name=f"{experiment.team.slug}-api"
        )

        channel = await sync_to_async(ApiChannel)(experiment, channel_model, experiment_session=None, user=user)
        channel.message = BaseMessage(participant_id=participant_id, message_text="Hello")

        # Ensure session exists
        await channel._aensure_sessions_exists()

        # Verify session was created
        assert channel.experiment_session is not None
        assert channel.experiment_session.participant.identifier == participant_id
        assert channel.experiment_session.chat is not None
