from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat
from apps.experiments.models import ExperimentSession, Participant
from apps.pipelines.nodes.base import PipelineState


@pytest.mark.asyncio()
class TestAsyncNodeExecution:
    """Tests for async node execution."""

    async def test_execute_sub_agent(self, async_experiment_with_pipeline, db):
        """Test async node execution."""

        from apps.pipelines.nodes.llm_node import aexecute_sub_agent

        experiment = async_experiment_with_pipeline

        # Create session
        participant, _ = await Participant.objects.aget_or_create(
            identifier="test-participant", team=experiment.team, defaults={"platform": ChannelPlatform.API}
        )

        chat = await Chat.objects.acreate(team=experiment.team)
        session = await ExperimentSession.objects.acreate(
            team=experiment.team, experiment=experiment, participant=participant, chat=chat
        )

        # Create mock node with required attributes
        node = MagicMock()
        node.name = "Test Node"
        node.node_id = "test-node-id"
        node.synthetic_voice_id = None

        # Create input state
        state = PipelineState(
            messages=["Hello"],
            last_node_input="Hello",
            experiment_session=session,
            participant_data={},
            session_state={},
        )

        # Mock agent invocation and processing
        with (
            patch("apps.pipelines.nodes.llm_node.build_node_agent") as mock_build,
            patch("apps.pipelines.nodes.llm_node._aprocess_agent_output") as mock_process,
            patch("apps.pipelines.nodes.llm_node._asave_node_history") as mock_save,
            patch("apps.pipelines.nodes.llm_node.format_multimodal_input") as mock_format,
        ):
            mock_agent = AsyncMock()
            mock_agent.ainvoke = AsyncMock(
                return_value={
                    "messages": [type("Message", (), {"content": "Test response"})()],
                    "participant_data": {},
                    "session_state": {},
                }
            )
            mock_build.return_value = mock_agent
            mock_process.return_value = ("Test response", {"cited_files": [], "generated_files": []})
            mock_save.return_value = None
            mock_format.return_value = "Hello"

            # Execute node
            result = await aexecute_sub_agent(node, state)

            assert result is not None
            assert result["messages"][0] == "Test response"
