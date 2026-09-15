"""An error the participant can act on must leave no operator-visible failure behind.

The orchestrator tests prove the pipeline stops re-raising, and the stage-span tests
prove the stage's own span stays clean. Neither reaches the spans opened inside the
bot run (`Run Pipeline` in `PipelineBot.process_input`, and the node spans under it),
which is where the unsupported-attachment case actually raises. This runs a real
pipeline through a real tracing service to pin the trace status and the notification.
"""

from unittest import mock

import pytest

from apps.channels.datamodels import Attachment, BaseMessage
from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.stages.core import BotInteractionStage
from apps.channels.tests.channels.conftest import StubCallbacks, make_context
from apps.chat.bots import PipelineBot
from apps.ocs_notifications.models import NotificationEvent
from apps.pipelines.tests.utils import create_pipeline_model, end_node, llm_response_with_prompt_node, start_node
from apps.service_providers.tracing import TracingService
from apps.trace.models import Trace, TraceStatus
from apps.utils.factories.experiment import ExperimentSessionFactory, VersionedExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import build_fake_llm_service


def _bmp_attachment():
    """An image type no provider accepts -- what format_multimodal_input rejects."""
    return Attachment(
        file_id=1,
        type="code_interpreter",
        name="holiday.bmp",
        size=100,
        content_type="image/bmp",
        download_link="http://localhost:8000/f/1",
    )


@pytest.mark.django_db()
@mock.patch("apps.channels.pipeline.EventBot")
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_unsupported_attachment_leaves_no_error_trace_or_notification(get_llm_service, event_bot_cls):
    event_bot_cls.return_value.get_user_message.return_value = "That image type is not supported -- try a PNG."
    get_llm_service.return_value = build_fake_llm_service(responses=["unused"])
    provider = LlmProviderFactory.create()
    provider_model = LlmProviderModelFactory.create()

    # Published, because trace-error notifications only fire for a published version.
    experiment = VersionedExperimentFactory.create()
    create_pipeline_model(
        [
            start_node(),
            llm_response_with_prompt_node(str(provider.id), str(provider_model.id), prompt="Be helpful."),
            end_node(),
        ],
        pipeline=experiment.pipeline,
    )
    session = ExperimentSessionFactory.create(experiment=experiment)
    bot = PipelineBot(session, experiment, TracingService.create_for_experiment(experiment))
    ctx = make_context(
        experiment=experiment,
        experiment_session=session,
        bot=bot,
        trace_service=bot.trace_service,
        user_query="what is in this picture?",
        message=BaseMessage(
            participant_id=session.participant.identifier,
            message_text="what is in this picture?",
            attachments=[_bmp_attachment()],
        ),
        callbacks=StubCallbacks(),
    )
    pipeline = MessageProcessingPipeline(core_stages=[BotInteractionStage()], terminal_stages=[])

    before_notifications = set(NotificationEvent.objects.values_list("id", flat=True))
    before_traces = set(Trace.objects.values_list("id", flat=True))

    with bot.trace_service.trace("test-trace", session=session, inputs={}):
        pipeline.process(ctx)

    assert ctx.early_exit_response == "That image type is not supported -- try a PNG."

    trace = Trace.objects.exclude(id__in=before_traces).get()
    assert trace.status == TraceStatus.SUCCESS
    assert not NotificationEvent.objects.exclude(id__in=before_notifications).exists()
