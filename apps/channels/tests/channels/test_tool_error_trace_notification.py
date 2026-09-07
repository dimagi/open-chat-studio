"""Regression coverage for dimagi/open-chat-studio#4420.

Unit tests elsewhere prove the two halves in isolation: `CustomBaseTool._run` now lets
exceptions propagate (apps/chat/tests/test_tools.py), and `OCSCallbackHandler.on_tool_error`
records the error on the tracer (apps/service_providers/tests/test_ocs_tracer.py). Neither
proves what actually happens during a real pipeline run. This does: a real `CustomBaseTool`
raising inside a real agent turn now aborts the turn instead of being swallowed -- exercising
the same pipeline catch-all an LLM/chain failure already uses -- and still produces a
trace-linked notification via the pre-existing `on_tool_error` path, not a new mechanism.
"""

from typing import ClassVar
from unittest import mock

import pytest
from langchain_core.messages import AIMessage, ToolCall

from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.stages.core import BotInteractionStage
from apps.channels.tests.channels.conftest import StubCallbacks, make_context
from apps.chat.agent import tools
from apps.chat.bots import PipelineBot
from apps.experiments.models import AgentTools
from apps.ocs_notifications.models import NotificationEvent
from apps.pipelines.tests.utils import create_pipeline_model, end_node, llm_response_with_prompt_node, start_node
from apps.service_providers.tracing import TracingService
from apps.utils.factories.experiment import ExperimentSessionFactory, VersionedExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import build_fake_llm_service


def _tool_call(name, args):
    return AIMessage(tool_calls=[ToolCall(name=name, args=args, id="call-1")], content="")


@pytest.mark.django_db()
@mock.patch("apps.chat.bots.EventBot.get_user_message")
@mock.patch("apps.pipelines.nodes.llm_node._get_configured_tools")
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_tool_error_aborts_turn_and_creates_trace_linked_notification(
    get_llm_service, get_configured_tools, mock_event_bot_message
):
    provider = LlmProviderFactory.create()
    provider_model = LlmProviderModelFactory.create()
    # Force the pipeline catch-all's EventBot path to fail over to its default text, so the
    # assertion below doesn't depend on a second fake LLM call.
    mock_event_bot_message.side_effect = RuntimeError("EventBot unavailable in test")

    class RaisingTool(tools.CustomBaseTool):
        name: str = AgentTools.UPDATE_PARTICIPANT_DATA
        description: str = "Raises to exercise the real trace-error notification path"
        requires_callbacks: ClassVar[bool] = False

        def action(self, *args, **kwargs):
            raise ValueError("simulated tool failure for #4420 regression test")

    get_configured_tools.return_value = [RaisingTool()]
    get_llm_service.return_value = build_fake_llm_service(
        responses=[_tool_call(AgentTools.UPDATE_PARTICIPANT_DATA, {"key": "k", "value": "v"})]
    )

    # A published version -- trace-error notifications only fire for one (matches
    # pipeline/span-error behaviour, see OCSTracer._fire_error_notification_if_needed).
    experiment = VersionedExperimentFactory.create()
    create_pipeline_model(
        [
            start_node(),
            llm_response_with_prompt_node(
                str(provider.id),
                str(provider_model.id),
                prompt="Be helpful. {participant_data}",
                tools=[AgentTools.UPDATE_PARTICIPANT_DATA],
            ),
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
        user_query="hi",
        callbacks=StubCallbacks(),
    )
    pipeline = MessageProcessingPipeline(core_stages=[BotInteractionStage()], terminal_stages=[])

    before_ids = set(NotificationEvent.objects.filter(team=experiment.team).values_list("id", flat=True))

    with bot.trace_service.trace("test-trace", session=session, inputs={}):
        with pytest.raises(ValueError, match="simulated tool failure"):
            pipeline.process(ctx)

    # The pre-existing pipeline catch-all still delivered a graceful reply -- the turn aborts,
    # but the user isn't left with a raw error.
    assert ctx.early_exit_response == MessageProcessingPipeline.DEFAULT_ERROR_RESPONSE_TEXT

    # The failure still produced a trace-linked notification, the same mechanism pipeline/span
    # errors already use -- this is the actual behaviour #4420 asked for.
    notification = (
        NotificationEvent.objects.filter(team=experiment.team)
        .exclude(id__in=before_ids)
        .order_by("-created_at")
        .first()
    )
    assert notification is not None
    assert notification.title.startswith("Tool Error Failed")
    assert notification.links.get("View Trace")
