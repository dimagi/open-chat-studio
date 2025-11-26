import pytest
from asgiref.sync import sync_to_async

from apps.channels.models import ChannelPlatform
from apps.chat.bots import PipelineBot
from apps.chat.models import Chat, ChatMessageType
from apps.experiments.models import ExperimentSession, Participant
from apps.service_providers.tracing.service import TracingService


@pytest.mark.asyncio()
class TestAsyncMessagePersistence:
    """Tests for async message persistence in PipelineBot."""

    async def test_save_message_to_history(self, async_experiment_with_pipeline, db):
        """Test async message saving."""
        experiment = async_experiment_with_pipeline

        # Create session
        participant, _ = await Participant.objects.aget_or_create(
            identifier="test-participant", team=experiment.team, defaults={"platform": ChannelPlatform.API}
        )

        chat = await Chat.objects.acreate(team=experiment.team)
        session = await ExperimentSession.objects.acreate(
            team=experiment.team, experiment=experiment, participant=participant, chat=chat
        )

        # Create bot
        trace_service = TracingService.create_for_experiment(experiment)
        bot = await sync_to_async(PipelineBot)(session=session, experiment=experiment, trace_service=trace_service)

        # Save a message
        message = await bot._asave_message_to_history(
            message="Test message", type_=ChatMessageType.HUMAN, metadata={"test": "data"}
        )

        assert message is not None
        assert message.content == "Test message"
        assert message.chat_id == chat.id

        # Verify message was saved
        count = await chat.messages.acount()
        assert count == 1
