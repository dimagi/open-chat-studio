from unittest.mock import Mock

import pytest
from django.test import TestCase

from apps.channels.models import ExperimentChannel
from apps.chat.channels import _start_experiment_session
from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.tasks import _get_latest_sessions_for_participants
from apps.experiments.models import ConsentForm, Experiment, ExperimentSession, SessionStatus
from apps.service_providers.models import LlmProvider, LlmProviderModel, TraceProvider
from apps.service_providers.tests.mock_tracer import MockTracer
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.teams.models import Team
from apps.users.models import CustomUser
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_llm


class TasksTest(TestCase):
    def setUp(self):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.team = Team.objects.create(name="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            prompt_text="You are a helpful assistant",
            consent_form=ConsentForm.get_default(self.team),
            llm_provider=LlmProvider.objects.create(
                name="test",
                type="openai",
                team=self.team,
                config={
                    "openai_api_key": "123123123",
                },
            ),
            llm_provider_model=LlmProviderModel.objects.create(
                team=self.team,
                type="openai",
                name="gpt-4",
            ),
        )
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel",
            team=self.team,
            experiment=self.experiment,
            extra_data={"bot_token": "123123123"},
            platform="telegram",
        )
        self.experiment_session = self._add_session(self.experiment)

    def test_getting_ping_message_saves_history(self):
        expected_ping_message = "Hey, answer me!"

        provider = TraceProvider()
        provider.get_service = Mock(return_value=MockTracer())
        self.experiment_session.experiment.trace_provider = provider
        trace_service = TracingService.empty()
        trace_service.get_trace_metadata = lambda: {"trace_info": True}
        with mock_llm(responses=[expected_ping_message]):
            response = self.experiment_session._bot_prompt_for_user(
                "test", TraceInfo(name="Some message"), trace_service
            )
        messages = ChatMessage.objects.filter(chat=self.experiment_session.chat).all()
        # Only the AI message should be there
        assert len(messages) == 1
        assert messages[0].message_type == "ai"
        assert response == expected_ping_message
        assert messages[0].content == expected_ping_message
        assert "trace_info" in messages[0].metadata

    def _add_session(self, experiment: Experiment, session_status: SessionStatus = SessionStatus.ACTIVE):
        return _start_experiment_session(
            experiment,
            experiment_channel=self.experiment_channel,
            participant_identifier=self.telegram_chat_id,
            session_status=session_status,
        )

    def _add_chats(self, experiment_session: ExperimentSession, last_message_type: ChatMessageType):
        ChatMessage.objects.create(chat=experiment_session.chat, message_type=ChatMessageType.HUMAN, content="Hi")
        if last_message_type == ChatMessageType.AI:
            ChatMessage.objects.create(
                chat=experiment_session.chat,
                message_type=ChatMessageType.AI,
                content="Hello. How can I assist you today?",
            )


@pytest.mark.django_db()
def test_get_latest_sessions_for_participants():
    p1_session1 = ExperimentSessionFactory()
    participant_1 = p1_session1.participant
    experiment = p1_session1.experiment
    ExperimentSessionFactory(experiment=experiment, participant=participant_1)
    part1_exp1_session3 = ExperimentSessionFactory(experiment=experiment, participant=participant_1)

    p2_session1 = ExperimentSessionFactory(experiment=experiment)
    participant_2 = p2_session1.participant
    part2_exp1_session2 = ExperimentSessionFactory(experiment=experiment, participant=participant_2)

    part2_exp2_session1 = ExperimentSessionFactory(participant=participant_2)
    assert part2_exp2_session1.team != part2_exp1_session2.team

    chat_ids = [participant_1.identifier, participant_2.identifier]
    sessions = _get_latest_sessions_for_participants(chat_ids, experiment_public_id=str(experiment.public_id))
    # Let's make sure the experiment was correctly filtered
    assert len(set([s.experiment for s in sessions])) == 1
    assert sessions[0].experiment == experiment

    # Let's make sure only the latest sessions of each participant is returned
    assert len(sessions) == 2
    session_ids = [s.id for s in sessions]
    assert part1_exp1_session3.id in session_ids
    assert part2_exp1_session2.id in session_ids
