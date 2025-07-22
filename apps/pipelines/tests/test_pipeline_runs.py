from unittest import mock

import pytest

from apps.annotations.models import TagCategories
from apps.chat.bots import PipelineBot
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import PipelineState
from apps.service_providers.tests.mock_tracer import MockTracer
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_transactional


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def experiment(pipeline):
    return ExperimentFactory(pipeline=pipeline)


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@django_db_transactional()
@pytest.mark.parametrize("save_input_to_history", [True, False])
def test_save_input_to_history(save_input_to_history, pipeline: Pipeline, session: ExperimentSession):
    input = "Hi"
    bot = PipelineBot(session=session, experiment=session.experiment, trace_service=TracingService.empty())
    bot.process_input(input, save_input_to_history=save_input_to_history)
    assert (
        session.chat.messages.filter(content="Hi", message_type=ChatMessageType.HUMAN).exists() == save_input_to_history
    )


@django_db_transactional()
def test_save_trace_metadata(pipeline: Pipeline, session: ExperimentSession):
    trace_service = TracingService([MockTracer()], 1, 1)
    with trace_service.trace("test", session, "bob"):
        bot = PipelineBot(session=session, experiment=session.experiment, trace_service=trace_service)
        bot.process_input("Hi")
    human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).first()
    assert "trace_info" in human_message.metadata
    ai_message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert "trace_info" in ai_message.metadata


@pytest.mark.django_db()
def test_save_metadata_and_tagging(pipeline: Pipeline, session: ExperimentSession):
    output_message_tags = [("test_tag_1", TagCategories.BOT_RESPONSE)]
    pipeline_state = PipelineState(messages=["Hi"], output_message_tags=output_message_tags)

    with mock.patch.object(ChatMessage, "create_and_add_tag") as mock_add_system_tag:
        bot = PipelineBot(session=session, experiment=session.experiment, trace_service=TracingService.empty())
        bot.invoke_pipeline(input_state=pipeline_state, pipeline=pipeline)
        for tag_value, category in output_message_tags:
            mock_add_system_tag.assert_any_call(tag_value, session.team, category or "")

        # add version tag also calls add system tag
        assert mock_add_system_tag.call_count == len(output_message_tags) + 1
