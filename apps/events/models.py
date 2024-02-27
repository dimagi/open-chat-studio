from datetime import datetime, timedelta

from django.db import models
from pytz import UTC

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
    params = models.JSONField(null=True, blank=True)  # The parameters for the specific action


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
        trigger_time = datetime.now().astimezone(UTC) - timedelta(seconds=self.delay)
        open_experiment_sessions = self.experiment.sessions.filter(ended_at=None)
        for session in open_experiment_sessions:
            try:
                if self.stats.get(session=session).trigger_count >= self.total_num_triggers:
                    continue
            except TriggerStats.DoesNotExist:
                # This trigger didn't run yet
                pass
            last_human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).last()
            if last_human_message and last_human_message.created_at < trigger_time:
                # TODO: should this use updated_at instead? When can you edit a chat message?
                sessions.append(session)
        return sessions

    def fire(self, session):
        try:
            result = super().fire(session)
        except Exception:
            # TODO handle exception
            raise

        self._increment_triggered_count(session)
        self._end_conversation(session)

        return result

    def _increment_triggered_count(self, session):
        now = datetime.now().astimezone(UTC)
        try:
            stats = self.stats.get(session=session)
            stats.trigger_count = stats.trigger_count + 1
            stats.last_triggered = now
            stats.save()
        except TriggerStats.DoesNotExist:
            TriggerStats.objects.create(trigger=self, session=session, trigger_count=1, last_triggered=now)

    def _end_conversation(self, session):
        if self.end_conversation and self.stats.get(session=session).trigger_count >= self.total_num_triggers:
            session.ended_at = datetime.now().astimezone(UTC)
            session.status = SessionStatus.PENDING_REVIEW
            session.save()


class TriggerStats(BaseModel):
    trigger = models.ForeignKey(TimeoutTrigger, on_delete=models.CASCADE, related_name="stats")
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="timout_trigger_stats")
    last_triggered = models.DateTimeField(null=True, help_text="The last time the trigger was fired for the session")
    trigger_count = models.IntegerField(
        default=0,
        help_text="The number of times this trigger was fired for the session",
    )

    class Meta:
        unique_together = ("trigger", "session")


# class EventTrigger(BaseTeamModel):
#     experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="event_triggers")
#     action = models.ForeignKey(EventAction, on_delete=models.CASCADE, related_name="triggers")
