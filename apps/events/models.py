from datetime import timedelta

from django.db import models
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
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
        """Finds all the timed out sessions where:
        - The last human message was sent at a time earlier than the trigger time
        - There have been fewer trigger attempts than the total number defined by the trigger
        """

        trigger_time = timezone.now() - timedelta(seconds=self.delay)

        last_human_message_time = (
            ChatMessage.objects.filter(chat__experiment_session=OuterRef("pk"), message_type=ChatMessageType.HUMAN)
            .order_by("-created_at")
            .values("created_at")[:1]
        )

        sessions = (
            ExperimentSession.objects.filter(
                experiment=self.experiment,
                ended_at=None,
            )
            .annotate(
                last_human_message_created_at=Subquery(last_human_message_time),
                success_event_log_count=Count("event_logs", filter=Q(event_logs__status=EventLogStatusChoices.SUCCESS)),
            )
            .filter(
                last_human_message_created_at__lt=trigger_time,
                last_human_message_created_at__isnull=False,
                success_event_log_count__lt=self.total_num_triggers,
            )
            .all()
        )
        return sessions

    def fire(self, session):
        try:
            result = super().fire(session)
            self.add_event_log(session, EventLogStatusChoices.SUCCESS)
        except Exception:
            self.add_event_log(session, EventLogStatusChoices.FAILURE)

        self._end_conversation(session)

        return result

    def add_event_log(self, session, status):
        self.event_logs.create(session=session, status=status)

    def _end_conversation(self, session):
        if self.end_conversation and not self._has_triggers_left(session):
            session.ended_at = timezone.now()
            session.status = SessionStatus.PENDING_REVIEW
            session.save()

    def _has_triggers_left(self, session):
        return (
            self.event_logs.filter(session=session, status=EventLogStatusChoices.SUCCESS).count()
            < self.total_num_triggers
        )


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
