import logging
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError

from apps.channels.datamodels import BaseMessage
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.stages.terminal import PersistenceStage
from apps.channels.tasks import handle_evaluation_message
from apps.chat.bots import EvalsBot
from apps.chat.exceptions import ChannelException, ProviderConfigurationError
from apps.chat.models import ChatMessage
from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.recorder import UsageContext
from apps.pipelines.tests.utils import create_pipeline_model, end_node, llm_response_node, start_node
from apps.service_providers.tracing.capture_tracer import CaptureTracer
from apps.service_providers.tracing.usage_tracer import UsageOnlyTracer
from apps.utils.factories.experiment import ChatbotFactory, ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.langchain import build_fake_llm_service


def test_requires_existing_session():
    with pytest.raises(ChannelException, match="requires an existing session"):
        EvaluationChannel(
            experiment=MagicMock(),
            experiment_channel=MagicMock(),
            experiment_session=None,
            participant_data={},
        )


@pytest.fixture()
def evals_experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


@pytest.fixture()
def evals_channel(evals_experiment):
    return ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)


@pytest.mark.django_db()
def test_get_team_evaluations_channel_is_idempotent(evals_experiment):
    """Repeated calls return the same team channel instead of creating duplicates"""
    channel1 = ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)
    channel2 = ExperimentChannel.objects.get_team_evaluations_channel(evals_experiment.team)
    assert channel1.id == channel2.id


@pytest.mark.django_db()
def test_team_evaluations_channel_is_unique_per_team(evals_experiment):
    """The DB constraint itself rejects a second evaluations channel for the same team"""
    team = evals_experiment.team
    ExperimentChannel.objects.get_team_evaluations_channel(team)

    with pytest.raises(IntegrityError):
        ExperimentChannel.objects.create(team=team, platform=ChannelPlatform.EVALUATIONS, name="another-evals-channel")


@pytest.mark.django_db()
class TestEvaluationChannelTracing:
    """An eval run leaves no `Trace` behind: it gets the billing tracer and the capture tracer,
    never OCSTracer or the experiment's external trace provider.
    """

    def _channel(self, experiment, channel, usage_tracer=None):
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
        return EvaluationChannel(
            experiment=experiment,
            experiment_channel=channel,
            experiment_session=session,
            participant_data={},
            usage_tracer=usage_tracer,
        )

    def test_capture_tracer_is_attached_even_when_nothing_is_billed(self, evals_experiment, evals_channel):
        channel = self._channel(evals_experiment, evals_channel)

        assert channel.trace_service._tracers == [channel._capture_tracer]
        assert isinstance(channel._capture_tracer, CaptureTracer)

    def test_usage_tracer_rides_beside_the_capture_tracer(self, evals_experiment, evals_channel):
        tracer = UsageOnlyTracer(UsageContext(team_id=evals_experiment.team_id, source=UsageSource.EVALUATION))

        channel = self._channel(evals_experiment, evals_channel, usage_tracer=tracer)

        assert channel.trace_service._tracers == [tracer, channel._capture_tracer]


@pytest.mark.django_db()
class TestEvaluationChannelEndToEnd:
    @patch("apps.channels.stages.core.EvalsBot")
    def test_processes_message_with_evals_bot(self, mock_evals_bot_cls, evals_experiment, evals_channel):
        mock_bot = MagicMock()
        mock_bot.process_input.return_value = ChatMessage(content="Bot response")
        mock_evals_bot_cls.return_value = mock_bot
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)
        participant_data = {"userid": "1234"}

        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data=participant_data,
        )
        message = BaseMessage(participant_id=session.participant.identifier, message_text="Hi")

        result = channel.new_user_message(message)

        assert isinstance(result, ChatMessage)
        assert result.content == "Bot response"
        mock_evals_bot_cls.assert_called_once()
        assert mock_evals_bot_cls.call_args.kwargs["participant_data"] == participant_data

    @patch("apps.channels.stages.core.enqueue_static_triggers")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_static_triggers_suppressed(self, mock_process, mock_triggers, evals_experiment, evals_channel):
        mock_process.return_value = ChatMessage(content="Bot response")
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data={},
        )
        channel.new_user_message(BaseMessage(participant_id=session.participant.identifier, message_text="Hi"))

        mock_triggers.delay.assert_not_called()

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_handle_evaluation_message_task(self, mock_process, evals_experiment, evals_channel):
        mock_process.return_value = ChatMessage(content="Bot response")
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        reply = handle_evaluation_message(
            experiment_version=evals_experiment,
            experiment_channel=evals_channel,
            message_text="Test evaluation message",
            session=session,
            participant_data={},
        )

        assert isinstance(reply.message, ChatMessage)
        assert reply.message.content == "Bot response"
        assert reply.error is None

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_handle_evaluation_message_returns_a_handled_configuration_error(
        self, mock_process, evals_experiment, evals_channel
    ):
        """The pipeline answers a configuration error instead of raising it; the caller still needs to know."""
        error = ProviderConfigurationError("The LLM provider account has no credit or quota remaining.")
        mock_process.side_effect = error
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        reply = handle_evaluation_message(
            experiment_version=evals_experiment,
            experiment_channel=evals_channel,
            message_text="Test evaluation message",
            session=session,
            participant_data={},
        )

        assert reply.message.content == MessageProcessingPipeline.DEFAULT_ERROR_RESPONSE_TEXT
        assert reply.error is error

    @patch("apps.channels.stages.core.EvalsBot")
    def test_reply_carries_the_bots_final_state_and_diff(self, mock_evals_bot_cls, evals_experiment, evals_channel):
        mock_bot = MagicMock(spec=EvalsBot)
        mock_bot.process_input.return_value = ChatMessage(content="Bot response")
        mock_bot.pipeline_output = {"outputs": {"end": {"message": "Bot response", "node_id": "e"}}}
        mock_bot.participant_data_diff = [("change", "plan", ("free", "pro"))]
        mock_evals_bot_cls.return_value = mock_bot
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)
        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data={},
        )

        reply = channel.generate_reply(BaseMessage(participant_id=session.participant.identifier, message_text="Hi"))

        assert reply.pipeline_state == mock_bot.pipeline_output
        assert reply.participant_data_diff == [("change", "plan", ("free", "pro"))]

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_reply_carries_an_unexpected_failure_and_its_spans(
        self, mock_process, evals_experiment, evals_channel, caplog
    ):
        error = RuntimeError("boom")
        mock_process.side_effect = error
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)
        channel = EvaluationChannel(
            experiment=evals_experiment,
            experiment_channel=evals_channel,
            experiment_session=session,
            participant_data={},
        )

        with caplog.at_level(logging.ERROR):
            reply = channel.generate_reply(
                BaseMessage(participant_id=session.participant.identifier, message_text="Hi")
            )

        assert f"session {session.id}" in caplog.text
        assert reply.error is error
        assert reply.message.content == ""
        assert reply.pipeline_state is None
        assert reply.participant_data_diff == []
        assert reply.spans == channel._capture_tracer.spans

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_reply_without_a_completed_run_has_no_state(self, mock_process, evals_experiment, evals_channel):
        mock_process.side_effect = ProviderConfigurationError(
            "The LLM provider account has no credit or quota remaining."
        )
        session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)

        reply = handle_evaluation_message(
            experiment_version=evals_experiment,
            experiment_channel=evals_channel,
            message_text="Hi",
            session=session,
            participant_data={},
        )

        assert reply.error is not None
        assert reply.pipeline_state is None
        assert reply.participant_data_diff == []
        assert reply.spans == []


@pytest.mark.django_db()
def test_evaluation_pipeline_never_persists_bot_side_messages(evals_experiment, evals_channel):
    session = ExperimentSessionFactory.create(experiment=evals_experiment, experiment_channel=evals_channel)
    channel = EvaluationChannel(
        experiment=evals_experiment,
        experiment_channel=evals_channel,
        experiment_session=session,
        participant_data={},
    )

    pipeline = channel._build_pipeline()

    assert not any(isinstance(stage, PersistenceStage) for stage in pipeline.terminal_stages)


@pytest.mark.django_db()
@patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_reply_carries_the_spans_the_run_produced(get_llm_service, evals_experiment, evals_channel):
    team = evals_experiment.team
    experiment = ChatbotFactory.create(team=team)
    provider = LlmProviderFactory.create(team=team)
    provider_model = LlmProviderModelFactory.create(team=team)
    llm_node = llm_response_node(str(provider.id), str(provider_model.id), name="Answer")
    create_pipeline_model([start_node(), llm_node, end_node()], pipeline=experiment.pipeline)
    experiment.pipeline.save()
    get_llm_service.return_value = build_fake_llm_service(responses=["Bot response"])
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=evals_channel)

    reply = handle_evaluation_message(
        experiment_version=experiment,
        experiment_channel=evals_channel,
        message_text="Hi",
        session=session,
        participant_data={},
    )

    assert reply.message.content == "Bot response"
    assert [(span.type, span.node_id, span.output) for span in reply.spans] == [("llm", llm_node["id"], "Bot response")]
    assert reply.pipeline_state is not None
    assert reply.pipeline_state["outputs"]["Answer"]["message"] == "Bot response"
