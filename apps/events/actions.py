from django.db.models import Case, DateTimeField, F, When

from apps.experiments.models import ExperimentSession
from apps.pipelines.models import PipelineChatHistoryModes, PipelineEventInputs
from apps.pipelines.nodes.base import PipelineState
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.utils.django_db import MakeInterval


class EventActionHandlerBase:
    """
    Base class for event action handlers.

    Methods:
        invoke:
            Executes the action.
        event_action_updated:
            Callback for whenever the associated action is updated.
    """

    def invoke(self, session, action): ...

    def event_action_updated(self, action): ...


class LogAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        last_message = session.chat.messages.last()
        if last_message:
            return last_message.content
        return ""


class EndConversationAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        from apps.events.models import StaticTriggerType

        session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_EVENT)
        return "Session ended"


class ScheduleTriggerAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        from apps.events.models import ScheduledMessage

        ScheduledMessage.objects.create(
            experiment=session.experiment, participant=session.participant, team=session.team, action=action
        )
        return f"A scheduled message was created for participant '{session.participant.identifier}'"

    def event_action_updated(self, action):
        """
        This method updates the scheduled_messages queryset by considering the following criteria:
        - Frequency and time period (delta change):
            - If the scheduled message's last_triggered_at field is None (it has not fired), the created_at field
            is used as the baseline for adding the new delta
            - If the scheduled message's last_triggered_at field is not None (it has fired before), that field is
            then used as the baseline for adding the new delta
        """
        params = action.params.copy()
        # We need to map `minutes` to `mins` for compatibility with MakeInterval
        if params["time_period"] == "minutes":
            params["time_period"] = "mins"

        (
            action.scheduled_messages.annotate(
                new_delta=MakeInterval(params["time_period"], params["frequency"]),
            )
            .filter(is_complete=False, custom_schedule_params={})
            .update(
                next_trigger_date=Case(
                    When(last_triggered_at__isnull=True, then=F("created_at") + F("new_delta")),
                    When(last_triggered_at__isnull=False, then=F("last_triggered_at") + F("new_delta")),
                    output_field=DateTimeField(),
                ),
            )
        )


class SendMessageToBotAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        try:
            message = action.params["message_to_bot"]
        except KeyError:
            message = "The user hasn't responded, please prompt them again."

        trace_info = TraceInfo(name="event", metadata={"action_type": action.action_type, "action_id": action.id})
        session.ad_hoc_bot_message(message, trace_info)

        last_message = session.chat.messages.last()
        if last_message:
            return last_message.content
        return ""


class PipelineStartAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        from apps.pipelines.models import Pipeline

        try:
            pipeline: Pipeline = Pipeline.objects.get(id=action.params["pipeline_id"])
        except KeyError:
            raise ValueError("The action is missing the pipeline id") from None
        except Pipeline.DoesNotExist:
            raise ValueError("The selected pipeline does not exist, maybe it was deleted?") from None
        try:
            input_type = action.params["input_type"]
        except KeyError:
            raise ValueError("The action is missing the input type") from None
        if input_type == PipelineEventInputs.FULL_HISTORY:
            messages = session.chat.get_langchain_messages()
        elif input_type == PipelineEventInputs.HISTORY_LAST_SUMMARY:
            messages = session.chat.get_langchain_messages_until_marker(marker=PipelineChatHistoryModes.SUMMARIZE)
        elif input_type == PipelineEventInputs.LAST_MESSAGE:
            last_message = session.chat.messages.last()
            if last_message:
                messages = [last_message.to_langchain_message()]
            else:
                messages = []
        else:
            raise ValueError(f"Unknown input type: {input_type}")

        input = "\n".join(f"{message.type}: {message.content}" for message in messages)
        participant_data = session.participant.global_data | session.participant_data_from_experiment
        state = PipelineState(
            messages=[input], experiment_session=session, participant_data=participant_data, session_state=session.state
        )
        trace_service = TracingService.create_for_experiment(session.experiment)
        with trace_service.trace(
            trace_name=f"{session.experiment.name} - event pipeline execution",
            session=session,
            inputs={"input": input},
            metadata={"action_type": action.action_type, "action_id": action.id, "params": action.params},
        ) as span:
            from apps.chat.bots import PipelineBot

            bot = PipelineBot(
                session=session,
                experiment=session.experiment_version,
                trace_service=trace_service,
            )
            output = bot.invoke_pipeline(
                input_state=state,
                pipeline=pipeline,
                save_run_to_history=False,
            )
            # does not support updating participant data or session state
            span.set_outputs({"response": output.content})
        return output.content
