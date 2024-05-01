from django.db.models import BooleanField, Case, DateTimeField, F, When
from langchain.memory.prompt import SUMMARY_PROMPT
from langchain.memory.summary import SummarizerMixin

from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.django_db import MakeInterval


class EventActionHandlerBase:
    def invoke(self, session, *args, **kwargs):
        ...

    def event_action_updated(self, action):
        ...

    def event_action_deleted(self, action):
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

        ScheduledMessage.objects.create(participant=session.participant, team=session.team, action=action)
        return ""

    def event_action_updated(self, action):
        """
        This method updates the scheduled_messages queryset by considering the following criteria:
        - Number of repetitions:
            - If new repetitions are greater than total_triggers, set is_complete to False.
            - If new repetitions are less than total_triggers, set is_complete to True.

        - Frequency and time period (delta change):
            - If the scheduled message's last_triggered_at field is None (it has not fired), the created_at field
            is used as the baseline for adding the new delta
            - If the scheduled message's last_triggered_at field is not None (it has fired before), that field is
            then used as the baseline for adding the new delta
        """
        (
            action.scheduled_messages.annotate(
                new_delta=MakeInterval(action.params["time_period"], action.params["frequency"]),
            ).update(
                is_complete=Case(
                    When(total_triggers__lt=action.params["repetitions"], then=False),
                    When(total_triggers__gte=action.params["repetitions"], then=True),
                    output_field=BooleanField(),
                ),
                next_trigger_date=Case(
                    When(last_triggered_at__isnull=True, then=F("created_at") + F("new_delta")),
                    When(last_triggered_at__isnull=False, then=F("last_triggered_at") + F("new_delta")),
                    output_field=DateTimeField(),
                ),
            )
        )

    def event_action_deleted(self, action):
        action.scheduled_messages.all().delete()


class SendMessageToBotAction(EventActionHandlerBase):
    def invoke(self, session: ExperimentSession, action) -> str:
        from apps.chat.tasks import bot_prompt_for_user, try_send_message

        try:
            message = action.params["message_to_bot"]
        except KeyError:
            message = "The user hasn't responded, please prompt them again."

        # TODO: experiment_session.send_bot_message
        ping_message = bot_prompt_for_user(session, prompt_instruction=message)
        try_send_message(experiment_session=session, message=ping_message)

        last_message = session.chat.messages.last()
        if last_message:
            return last_message.content
