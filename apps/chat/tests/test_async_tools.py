import pytest
from asgiref.sync import sync_to_async

from apps.channels.models import ChannelPlatform
from apps.chat.agent.tools import UpdateParticipantDataTool
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant


@pytest.mark.asyncio()
class TestAsyncTools:
    """Tests for async tool execution."""

    async def test_update_participant_data_tool(self, async_experiment_with_pipeline, db):
        """Test async participant data update tool."""
        experiment = async_experiment_with_pipeline

        # Create session
        participant, _ = await Participant.objects.aget_or_create(
            identifier="test-participant", team=experiment.team, defaults={"platform": ChannelPlatform.API}
        )

        chat = await Chat.objects.acreate(team=experiment.team)
        session = await ExperimentSession.objects.acreate(
            team=experiment.team, experiment=experiment, participant=participant, chat=chat
        )

        # Create tool
        tool = await sync_to_async(UpdateParticipantDataTool)(experiment_session=session)

        # Execute tool
        result = await tool._arun(key="test_key", value="test_value", tool_call_id="test-123")

        # Verify result is a Command
        from langgraph.types import Command

        assert isinstance(result, Command)
        assert result.update["participant_data"]["test_key"] == "test_value"
