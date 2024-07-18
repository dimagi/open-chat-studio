import logging
from datetime import timedelta
from functools import cached_property

from dateutil.relativedelta import relativedelta
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import F, Func, OuterRef, Q, Subquery
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.events import actions
from apps.events.const import TOTAL_FAILURES
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel
from apps.utils.time import pretty_date

logger = logging.getLogger(__name__)


ACTION_HANDLERS = {
    "end_conversation": actions.EndConversationAction,
    "log": actions.LogAction,
    "pipeline_start": actions.PipelineStartAction,
    "schedule_trigger": actions.ScheduleTriggerAction,
    "send_message_to_bot": actions.SendMessageToBotAction,
    "summarize": actions.SummarizeConversationAction,
}


class EventActionType(models.TextChoices):
    LOG = ("log", "Log the last message")
    END_CONVERSATION = ("end_conversation", "End the conversation")
    SUMMARIZE = ("summarize", "Summarize the conversation")
    SEND_MESSAGE_TO_BOT = ("send_message_to_bot", "Prompt the bot to message the user")
    SCHEDULETRIGGER = ("schedule_trigger", "Trigger a schedule")
    PIPELINE_START = ("pipeline_start", "Start a pipeline")


class EventAction(BaseModel):
    action_type = models.CharField(choices=EventActionType.choices)
    params = models.JSONField(blank=True, default=dict)

    @transaction.atomic()
    def save(self, *args, **kwargs):
        if not self.id:
            return super().save(*args, **kwargs)
        else:
            res = super().save(*args, **kwargs)
            handler = ACTION_HANDLERS[self.action_type]()
            handler.event_action_updated(self)
            return res


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
    log = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]


class StaticTriggerType(models.TextChoices):
    CONVERSATION_END = ("conversation_end", "The conversation ends")
    LAST_TIMEOUT = ("last_timeout", "The last timeout occurs")
    HUMAN_SAFETY_LAYER_TRIGGERED = ("human_safety_layer_triggered", "The safety layer is triggered by a human")
    BOT_SAFETY_LAYER_TRIGGERED = ("bot_safety_layer_triggered", "The safety layer is triggered by a bot")
    CONVERSATION_START = ("conversation_start", "A new conversation is started")
    NEW_HUMAN_MESSAGE = ("new_human_message", "A new human message is received")
    NEW_BOT_MESSAGE = ("new_bot_message", "A new bot message is received")
    PARTICIPANT_JOINED_EXPERIMENT = ("participant_joined", "A new participant joined the experiment")


class StaticTrigger(BaseModel):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="static_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="static_triggers")
    type = models.CharField(choices=StaticTriggerType.choices, db_index=True)
    event_logs = GenericRelation(EventLog)

    @property
    def trigger_type(self):
        return "StaticTrigger"

    def fire(self, session):
        try:
            result = ACTION_HANDLERS[self.action.action_type]().invoke(session, self.action)
            self.event_logs.create(session=session, status=EventLogStatusChoices.SUCCESS, log=result)
            return result
        except Exception as e:
            logging.exception(e)
            self.event_logs.create(session=session, status=EventLogStatusChoices.FAILURE, log=str(e))

    @transaction.atomic()
    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self.action.delete(*args, **kwargs)
        return result


class TimeoutTrigger(BaseModel):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="timeout_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="timeout_triggers")
    delay = models.PositiveIntegerField(
        help_text="Seconds to wait after last response before triggering action",
    )
    total_num_triggers = models.IntegerField(
        default=1,
        help_text="The number of times to trigger the action",
    )
    event_logs = GenericRelation(EventLog)

    @property
    def trigger_type(self):
        return "TimeoutTrigger"

    def timed_out_sessions(self):
        """Finds all the timed out sessions where:
        - The last human message was sent at a time earlier than the trigger time
        - There have been fewer trigger attempts than the total number defined by the trigger
        """
        from apps.chat.tasks import STATUSES_FOR_COMPLETE_CHATS

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
        failure_count_for_last_message = (
            EventLog.objects.filter(
                session=OuterRef("pk"),
                chat_message_id=Subquery(last_human_message_id),
                status=EventLogStatusChoices.FAILURE,
            )
            .annotate(count=Func(F("chat_message_id"), function="Count"))
            .values("count")
        )

        sessions = (
            ExperimentSession.objects.filter(
                experiment=self.experiment,
                ended_at=None,
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .annotate(
                last_human_message_created_at=Subquery(last_human_message_created_at),
                log_count=Subquery(log_count_for_last_message),
                failure_count=Subquery(failure_count_for_last_message),
            )
            .filter(
                last_human_message_created_at__lt=trigger_time,
                last_human_message_created_at__isnull=False,
            )  # The last message was received before the trigger time
            .filter(
                Q(log_count__lt=self.total_num_triggers) | Q(log_count__isnull=True)
            )  # There were either no tries yet, or fewer tries than the required number for this message
            .filter(
                Q(failure_count__lt=TOTAL_FAILURES)
                # There are still failures left
            )
        )
        return sessions.select_related("experiment_channel", "experiment").all()

    def fire(self, session) -> str | None:
        last_human_message = ChatMessage.objects.filter(
            chat_id=session.chat_id,
            message_type=ChatMessageType.HUMAN,
        ).last()

        result = None

        try:
            result = ACTION_HANDLERS[self.action.action_type]().invoke(session, self.action)
            self.event_logs.create(
                session=session, chat_message=last_human_message, status=EventLogStatusChoices.SUCCESS, log=result
            )
        except Exception as e:
            self.event_logs.create(
                session=session, chat_message=last_human_message, status=EventLogStatusChoices.FAILURE, log=str(e)
            )

        if not self._has_triggers_left(session, last_human_message):
            from apps.events.tasks import enqueue_static_triggers

            enqueue_static_triggers.delay(session.id, StaticTriggerType.LAST_TIMEOUT)

        return result

    def _has_triggers_left(self, session, message):
        has_succeeded = (
            self.event_logs.filter(
                session=session,
                chat_message=message,
                status=EventLogStatusChoices.SUCCESS,
            ).count()
            >= self.total_num_triggers
        )
        failed = (
            self.event_logs.filter(
                session=session,
                chat_message=message,
                status=EventLogStatusChoices.FAILURE,
            ).count()
            >= TOTAL_FAILURES
        )

        return not (has_succeeded or failed)


class TimePeriod(models.TextChoices):
    HOURS = ("hours", "Hours")
    DAYS = ("days", "Days")
    WEEKS = ("weeks", "Weeks")
    MONTHS = ("months", "Months")


class ScheduledMessage(BaseTeamModel):
    action = models.ForeignKey(
        EventAction, on_delete=models.CASCADE, related_name="scheduled_messages", null=True, default=None
    )
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="scheduled_messages")
    participant = models.ForeignKey(
        "experiments.Participant", on_delete=models.CASCADE, related_name="schduled_messages"
    )
    next_trigger_date = models.DateTimeField(null=True)
    last_triggered_at = models.DateTimeField(null=True)
    total_triggers = models.IntegerField(default=0)
    is_complete = models.BooleanField(default=False)
    custom_schedule_params = models.JSONField(blank=True, default=dict)

    class Meta:
        indexes = [models.Index(fields=["is_complete"])]

    def save(self, *args, **kwargs):
        if not self.next_trigger_date:
            params = {}
            if self.custom_schedule_params:
                params = self.custom_schedule_params
            elif self.action:
                params = self.action.params
            delta = relativedelta(**{params["time_period"]: params["frequency"]})
            self.next_trigger_date = timezone.now() + delta
        super().save(*args, **kwargs)

    def safe_trigger(self):
        """This wraps a call to the _trigger method in a try-catch block"""
        try:
            self._trigger()
        except Exception as e:
            logger.exception(f"An error occured while trying to send scheduled messsage {self.id}. Error: {e}")

    def _trigger(self):
        delta = relativedelta(**{self.action.params["time_period"]: self.action.params["frequency"]})
        utc_now = timezone.now()

        experiment_id = self.action.params.get("experiment_id", self.experiment.id)
        experiment_session = self.participant.get_latest_session(experiment=self.experiment)
        experiment_to_use = Experiment.objects.get(id=experiment_id)
        experiment_session.ad_hoc_bot_message(
            self.action.params["prompt_text"], fail_silently=False, use_experiment=experiment_to_use
        )

        self.last_triggered_at = utc_now
        self.total_triggers += 1
        if self.total_triggers >= self.action.params["repetitions"]:
            self.is_complete = True
        else:
            self.next_trigger_date = utc_now + delta

        self.save()

    @cached_property
    def name(self) -> str:
        return self.action.params["name"]

    @cached_property
    def frequency(self) -> str:
        return self.action.params["frequency"]

    @cached_property
    def time_period(self) -> str:
        return self.action.params["time_period"]

    @cached_property
    def repetitions(self) -> str:
        return self.action.params["repetitions"]

    def as_string(self, as_timezone: str | None = None):
        schedule = f"{self.name}: Every {self.frequency} {self.time_period}, {self.repetitions} times"
        if self.time_period not in ["hour", "day"]:
            weekday = self.next_trigger_date.strftime("%A")
            schedule = (
                f"{self.name}: Every {self.frequency} {self.time_period} on {weekday} for {self.repetitions} times"
            )
        next_trigger = pretty_date(self.next_trigger_date, as_timezone=as_timezone)
        return f"{schedule} (next trigger is {next_trigger})"

    def __str__(self):
        return self.as_string()
