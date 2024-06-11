from django.db.models import Case, DateTimeField, F, When
from langchain.memory.prompt import SUMMARY_PROMPT
from langchain.memory.summary import SummarizerMixin

from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.models import PipelineEventInputs
from apps.pipelines.nodes.base import PipelineState
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

    def invoke(self, session, *args, **kwargs):
        ...

    def event_action_updated(self, action):
        ...


class LogAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        last_message = session.chat.messages.last()
        if last_message:
            return last_message.content


class EndConversationAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        session.end()
        return "Session ended"


class SummarizeConversationAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        try:
            prompt = action.params["prompt"]
        except KeyError:
            prompt = SUMMARY_PROMPT
        history = session.chat.get_langchain_messages_until_summary()
        current_summary = history.pop(0).content if history[0].type == ChatMessageType.SYSTEM else ""
        messages = session.chat.get_langchain_messages()
        summary = SummarizerMixin(llm=session.experiment.get_chat_model(), prompt=prompt).predict_new_summary(
            messages, current_summary
        )

        return summary


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
        (
            action.scheduled_messages.annotate(
                new_delta=MakeInterval(action.params["time_period"], action.params["frequency"]),
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

        session.ad_hoc_bot_message(instruction_prompt=message)

        last_message = session.chat.messages.last()
        if last_message:
            return last_message.content


class PipelineStartAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        from apps.pipelines.models import Pipeline

        try:
            pipeline: Pipeline = Pipeline.objects.get(id=action.params["pipeline_id"])
        except KeyError:
            raise ValueError("The action is missing the pipeline id")
        except Pipeline.DoesNotExist:
            raise ValueError("The selected pipeline does not exist, maybe it was deleted?")
        try:
            input_type = action.params["input_type"]
        except KeyError:
            raise ValueError("The action is missing the input type")
        if input_type == PipelineEventInputs.FULL_HISTORY:
            messages = session.chat.get_langchain_messages()
            input = "\n".join(message.pretty_repr() for message in messages)
        elif input_type == PipelineEventInputs.HISTORY_LAST_SUMMARY:
            messages = session.chat.get_langchain_messages_until_summary()
            input = "\n".join(message.pretty_repr() for message in messages)
        elif input_type == PipelineEventInputs.LAST_MESSAGE:
            input = session.chat.messages.last().to_langchain_message().pretty_repr()
        return pipeline.invoke(PipelineState(messages=[input]))
