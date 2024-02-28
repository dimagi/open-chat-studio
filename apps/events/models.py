from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.chat.models import ChatMessageType
from apps.events.actions import log
from apps.experiments.models import Experiment, ExperimentSession, SessionStatus
from apps.utils.models import BaseModel

ACTION_FUNCTIONS = {"log": log}


class EventActionType(models.TextChoices):
    LOG = "log"  # Prints the last message
    SUMMARIZE = "summarize"  # requires prompt


class EventAction(BaseModel):
    action_type = models.CharField(choices=EventActionType.choices)
    params = models.JSONField(blank=True, default=dict)


class BaseTrigger(BaseModel):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="action")

    class Meta:
        abstract = True

    def fire(self, session):
        return ACTION_FUNCTIONS[self.action.action_type](session)


class TimeoutTrigger(BaseTrigger):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="timeout_triggers")
    delay = models.PositiveIntegerField(
        help_text="The amount of time in seconds to fire this trigger.",
    )
    total_num_triggers = models.IntegerField(
        default=1,
        help_text="The number of times to fire this trigger",
    )
    end_conversation = models.BooleanField(
        default=True,
        help_text="Whether to end the conversation after all the triggers have expired",
    )

    def timed_out_sessions(self):
        sessions = []
        trigger_time = timezone.now() - timedelta(seconds=self.delay)
        open_experiment_sessions = self.experiment.sessions.filter(ended_at=None)
        for session in open_experiment_sessions:
            if not self.has_triggers_left(session):
                continue
            last_human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).last()
            if last_human_message and last_human_message.created_at < trigger_time:
                # TODO: should this use updated_at instead? When can you edit a chat message?
                sessions.append(session)
        return sessions

    def fire(self, session):
        try:
            result = super().fire(session)
            self.add_event_log(session, EventLogStatusChoices.SUCCESS)
        except Exception:
            self.add_event_log(session, EventLogStatusChoices.FAILURE)

        self._end_conversation(session)

        return result

    def has_triggers_left(self, session):
        return (
            self.event_logs.filter(session=session, status=EventLogStatusChoices.SUCCESS).count()
            < self.total_num_triggers
        )

    def add_event_log(self, session, status):
        self.event_logs.create(session=session, status=status)

    def _end_conversation(self, session):
        if self.end_conversation and not self.has_triggers_left(session):
            session.ended_at = timezone.now()
            session.status = SessionStatus.PENDING_REVIEW
            session.save()


class EventLogStatusChoices(models.TextChoices):
    SUCCESS = "success"
    FAILURE = "failure"


class EventLog(BaseModel):
    trigger = models.ForeignKey(TimeoutTrigger, on_delete=models.CASCADE, related_name="event_logs")
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="event_logs")
    status = models.CharField(choices=EventLogStatusChoices.choices)


# class EventTrigger(BaseTeamModel):
#     experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="event_triggers")
#     action = models.ForeignKey(EventAction, on_delete=models.CASCADE, related_name="triggers")
