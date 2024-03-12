from datetime import timedelta

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, Func, OuterRef, Q, Subquery
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.actions import end_conversation, log, summarize_conversation
from apps.experiments.models import Experiment, ExperimentSession
from apps.utils.models import BaseModel

ACTION_FUNCTIONS = {"log": log, "end_conversation": end_conversation, "summarize": summarize_conversation}


class EventActionType(models.TextChoices):
    LOG = ("log", "Log the last message")
    END_CONVERSATION = ("end_conversation", "End the conversation")
    SUMMARIZE = ("summarize", "Summarize the conversation")


class EventAction(BaseModel):
    action_type = models.CharField(choices=EventActionType.choices)
    params = models.JSONField(blank=True, default=dict)


class EventLogStatusChoices(models.TextChoices):
    SUCCESS = "success"
    FAILURE = "failure"


class EventLog(BaseModel):
    # StaticTrigger or TimeoutTrigger
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="event_logs")
    chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="event_logs", null=True, blank=True
    )
    status = models.CharField(choices=EventLogStatusChoices.choices)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]


class StaticTriggerType(models.TextChoices):
    CONVERSATION_END = ("conversation_end", "The conversation ends")
    LAST_TIMEOUT = ("last_timeout", "The last timeout occurs")


class StaticTrigger(BaseModel):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="static_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="static_triggers")
    type = models.CharField(choices=StaticTriggerType.choices, db_index=True)
    event_logs = GenericRelation(EventLog)

    def fire(self, session):
        try:
            result = ACTION_FUNCTIONS[self.action.action_type](session, self.action.params)
            self.event_logs.create(session=session, status=EventLogStatusChoices.SUCCESS)
        except Exception:
            self.event_logs.create(session=session, status=EventLogStatusChoices.FAILURE)

        return result


class TimeoutTrigger(BaseModel):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="timeout_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="timeout_triggers")
    delay = models.PositiveIntegerField(
        help_text="The amount of time in seconds to fire this trigger.",
    )
    total_num_triggers = models.IntegerField(
        default=1,
        help_text="The number of times to fire this trigger",
    )
    event_logs = GenericRelation(EventLog)

    def timed_out_sessions(self):
        """Finds all the timed out sessions where:
        - The last human message was sent at a time earlier than the trigger time
        - There have been fewer trigger attempts than the total number defined by the trigger
        """

        trigger_time = timezone.now() - timedelta(seconds=self.delay)

        last_human_message_created_at = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("pk"),
                message_type=ChatMessageType.HUMAN,
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        last_human_message_id = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("session_id"),
                message_type=ChatMessageType.HUMAN,
            )
            .order_by("-created_at")
            .values("id")[:1]
        )
        log_count_for_last_message = (
            EventLog.objects.filter(
                session=OuterRef("pk"),
                chat_message_id=Subquery(last_human_message_id),
                status=EventLogStatusChoices.SUCCESS,
            )
            .annotate(
                count=Func(F("chat_message_id"), function="Count")
            )  # We don't use Count here because otherwise Django wants to do a group_by, which messes up the subquery: https://stackoverflow.com/a/69031027
            .values("count")
        )

        sessions = (
            ExperimentSession.objects.filter(
                experiment=self.experiment,
                ended_at=None,
            )
            .annotate(
                last_human_message_created_at=Subquery(last_human_message_created_at),
                log_count=Subquery(log_count_for_last_message),
            )
            .filter(
                last_human_message_created_at__lt=trigger_time,
                last_human_message_created_at__isnull=False,
            )  # The last message was received before the trigger time
            .filter(
                Q(log_count__lt=self.total_num_triggers) | Q(log_count__isnull=True)
            )  # There were either no tries yet, or fewer tries than the required number for this message
        )
        return sessions.all()

    def fire(self, session):
        last_human_message = ChatMessage.objects.filter(chat_id=session.chat_id).last()
        try:
            result = ACTION_FUNCTIONS[self.action.action_type](session, self.action.params)
            self.add_event_log(session, last_human_message, EventLogStatusChoices.SUCCESS)
        except Exception:
            self.add_event_log(session, last_human_message, EventLogStatusChoices.FAILURE)

        if not self._has_triggers_left(session, last_human_message):
            from apps.events.tasks import enqueue_static_triggers

            enqueue_static_triggers.delay(session.id, StaticTriggerType.LAST_TIMEOUT)

        return result

    def add_event_log(self, session, message, status):
        self.event_logs.create(session=session, chat_message=message, status=status)

    def _has_triggers_left(self, session, message):
        return (
            self.event_logs.filter(
                session=session,
                chat_message=message,
                status=EventLogStatusChoices.SUCCESS,
            ).count()
            < self.total_num_triggers
        )
