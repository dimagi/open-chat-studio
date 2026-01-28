import pytest

from apps.annotations.models import TagCategories
from apps.chat.bots import PipelineBot
from apps.chat.models import ChatMessageType
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
def test_save_trace_metadata(pipeline: Pipeline, session: ExperimentSession):
    trace_service = TracingService([MockTracer()], 1, 1)
    with trace_service.trace("test", session):
        bot = PipelineBot(session=session, experiment=session.experiment, trace_service=trace_service)
        bot.process_input("Hi")
    ai_message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert "trace_info" in ai_message.metadata


@pytest.mark.django_db()
def test_output_message_tagging(pipeline: Pipeline, session: ExperimentSession):
    output_message_tags = [("test_tag_1", TagCategories.BOT_RESPONSE.value), ("user_tag", None)]
    pipeline_state = PipelineState(messages=["Hi"], output_message_tags=output_message_tags)

    bot = PipelineBot(session=session, experiment=session.experiment, trace_service=TracingService.empty())
    result = bot.invoke_pipeline(input_state=pipeline_state, pipeline=pipeline)

    tags = list(result.tags.all())
    version_tag = (f"v{session.experiment.version_number}-unreleased", TagCategories.EXPERIMENT_VERSION.value)
    _assert_tags(tags, output_message_tags + [version_tag])


@pytest.mark.django_db()
def test_session_tagging(pipeline: Pipeline, session: ExperimentSession):
    output_message_tags = [("test_tag_1", None), ("user_tag", "")]
    pipeline_state = PipelineState(messages=["Hi"], session_tags=output_message_tags)

    bot = PipelineBot(session=session, experiment=session.experiment, trace_service=TracingService.empty())
    bot.invoke_pipeline(input_state=pipeline_state, pipeline=pipeline)

    tags = list(session.chat.tags.all())
    _assert_tags(tags, output_message_tags)


def _assert_tags(object_tags, expected: list[tuple[str, str]]):
    assert len(object_tags) == len(expected)
    created_tags = set((tag.name, tag.category) for tag in object_tags)
    assert created_tags == set((name, category or "") for name, category in expected)
