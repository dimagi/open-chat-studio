from unittest.mock import AsyncMock, patch

import pytest

from apps.channels.models import ChannelPlatform


@pytest.mark.asyncio()
class TestAsyncIntegration:
    """End-to-end integration tests for async message handling."""

    async def test_ahandle_api_message_end_to_end(self, async_team_with_users, db):
        """Test complete async message flow with PipelineBot."""
        from asgiref.sync import sync_to_async

        from apps.channels.models import ExperimentChannel
        from apps.channels.tasks import ahandle_api_message
        from apps.users.models import CustomUser
        from apps.utils.factories.experiment import ExperimentFactory
        from apps.utils.factories.pipelines import PipelineFactory

        # Setup
        team = async_team_with_users
        user = await CustomUser.objects.filter(teams=team).afirst()

        # Create pipeline
        pipeline = await sync_to_async(PipelineFactory.create)(team=team)

        # Create experiment with pipeline
        experiment = await sync_to_async(ExperimentFactory.create)(team=team, pipeline=pipeline)

        # Create channel
        channel, _ = await ExperimentChannel.objects.aget_or_create(
            team=team, platform=ChannelPlatform.API, name=f"{team.slug}-api-channel"
        )

        # Mock graph execution to avoid real LLM calls
        with patch("apps.chat.bots.PipelineBot._arun_pipeline", new=AsyncMock()) as mock_run:
            mock_run.return_value = {
                "messages": ["Hello! How can I help you?"],
                "participant_data": {},
                "session_state": {},
            }

            # Send message
            response = await ahandle_api_message(
                user=user,
                experiment_version=experiment,
                experiment_channel=channel,
                message_text="Hello",
                participant_id="test-participant",
            )

            # Verify response
            assert response is not None
            assert response.content == "Hello! How can I help you?"

            # Verify message was saved
            assert response.id is not None

            # Verify session was created
            from apps.experiments.models import ExperimentSession

            session_count = await ExperimentSession.objects.filter(
                experiment=experiment, participant__identifier="test-participant"
            ).acount()
            assert session_count == 1

    async def test_ahandle_api_message_with_history(self, async_team_with_users, db):
        """Test async message handling with conversation history."""
        from asgiref.sync import sync_to_async

        from apps.channels.models import ExperimentChannel
        from apps.channels.tasks import ahandle_api_message
        from apps.users.models import CustomUser
        from apps.utils.factories.experiment import ExperimentFactory
        from apps.utils.factories.pipelines import PipelineFactory

        # Setup
        team = async_team_with_users
        user = await CustomUser.objects.filter(teams=team).afirst()

        pipeline = await sync_to_async(PipelineFactory.create)(team=team)
        experiment = await sync_to_async(ExperimentFactory.create)(team=team, pipeline=pipeline)

        channel, _ = await ExperimentChannel.objects.aget_or_create(
            team=team, platform=ChannelPlatform.API, name=f"{team.slug}-api-channel"
        )

        participant_id = "test-participant-history"

        # Mock graph execution
        with patch("apps.chat.bots.PipelineBot._arun_pipeline", new=AsyncMock()) as mock_run:
            mock_run.return_value = {
                "messages": ["Response"],
                "participant_data": {},
                "session_state": {},
            }

            # Send first message
            response1 = await ahandle_api_message(
                user=user,
                experiment_version=experiment,
                experiment_channel=channel,
                message_text="First message",
                participant_id=participant_id,
            )

            # Send second message (should reuse session)
            response2 = await ahandle_api_message(
                user=user,
                experiment_version=experiment,
                experiment_channel=channel,
                message_text="Second message",
                participant_id=participant_id,
            )

            # Verify both messages returned
            assert response1 is not None
            assert response2 is not None

            # Verify only one session was created (reused)
            from apps.experiments.models import ExperimentSession

            session_count = await ExperimentSession.objects.filter(
                experiment=experiment, participant__identifier=participant_id
            ).acount()
            assert session_count == 1

            # Verify messages were saved
            from apps.chat.models import ChatMessage

            message_count = await ChatMessage.objects.filter(chat=response1.chat).acount()
            # Should have 4 messages: human1, ai1, human2, ai2
            assert message_count == 4
