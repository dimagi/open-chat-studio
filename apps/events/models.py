import logging
from datetime import timedelta
from functools import cached_property

import pytz
from dateutil.relativedelta import relativedelta
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import F, Func, OuterRef, Q, Subquery, functions
from django.utils import timezone
from pytz.exceptions import UnknownTimeZoneError

from apps.chat.models import ChatMessage, ChatMessageType
from apps.events import actions
from apps.events.const import TOTAL_FAILURES
from apps.experiments.models import Experiment, ExperimentSession
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.service_providers.tracing import TraceInfo
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel
from apps.utils.slug import get_next_unique_id
from apps.utils.time import pretty_date

logger = logging.getLogger("ocs.events")

ACTION_HANDLERS = {
    "end_conversation": actions.EndConversationAction,
    "log": actions.LogAction,
    "pipeline_start": actions.PipelineStartAction,
    "schedule_trigger": actions.ScheduleTriggerAction,
    "send_message_to_bot": actions.SendMessageToBotAction,
    "summarize": actions.SummarizeConversationAction,
}


class StaticTriggerObjectManager(VersionsObjectManagerMixin, models.Manager):
    def published_versions(self):
        return self.filter(experiment__is_default_version=True)

    def get_published_version(self, trigger):
        return self.published_versions().get(working_version_id=trigger.get_working_version_id())


class TimeoutTriggerObjectManager(VersionsObjectManagerMixin, models.Manager):
    def published_versions(self):
        return self.filter(experiment__is_default_version=True)

    def get_published_version(self, trigger):
        return self.published_versions().get(working_version_id=trigger.get_working_version_id())


class EventActionType(models.TextChoices):
    LOG = ("log", "Log the last message")
    END_CONVERSATION = ("end_conversation", "End the conversation")
    SUMMARIZE = ("summarize", "Summarize the conversation")
    SEND_MESSAGE_TO_BOT = ("send_message_to_bot", "Prompt the bot to message the user")
    SCHEDULETRIGGER = ("schedule_trigger", "Trigger a schedule")
    PIPELINE_START = ("pipeline_start", "Start a pipeline")


class EventAction(BaseModel, VersionsMixin):
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


class StaticTrigger(BaseModel, VersionsMixin):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="static_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="static_triggers")
    type = models.CharField(choices=StaticTriggerType.choices, db_index=True)
    event_logs = GenericRelation(EventLog)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = StaticTriggerObjectManager()

    @property
    def trigger_type(self):
        return "StaticTrigger"

    def fire(self, session):
        working_version = self.get_working_version()
        try:
            result = ACTION_HANDLERS[self.action.action_type]().invoke(session, self.action)
            working_version.event_logs.create(session=session, status=EventLogStatusChoices.SUCCESS, log=result)
            return result
        except Exception as e:
            logging.exception(e)
            working_version.event_logs.create(session=session, status=EventLogStatusChoices.FAILURE, log=str(e))
        return None

    @transaction.atomic()
    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self.action.delete(*args, **kwargs)
        return result

    @transaction.atomic()
    def create_new_version(self, new_experiment: Experiment, is_copy: bool = False):
        """Create a duplicate and assign the `new_experiment` to it. Also duplicate all EventActions"""
        new_instance = super().create_new_version(save=False, is_copy=is_copy)
        new_instance.experiment = new_experiment
        new_instance.action = new_instance.action.create_new_version(is_copy=is_copy)
        new_instance.save()
        return new_instance

    @property
    def version_details(self):
        action_param_versions = []
        static_trigger_type = StaticTriggerType(self.type).label.lower()
        event_action_type = EventActionType(self.action.action_type).label
        # Static trigger group names should be user friendly
        group_name = f"When {static_trigger_type} then {event_action_type}"

        for name, value in self.action.params.items():
            action_param_versions.append(VersionField(group_name=group_name, name=name, raw_value=value))

        return VersionDetails(
            instance=self,
            fields=action_param_versions,
        )


class TimeoutTrigger(BaseModel, VersionsMixin):
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
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = TimeoutTriggerObjectManager()

    @transaction.atomic()
    def create_new_version(self, new_experiment: Experiment, is_copy: bool = False):
        """Create a duplicate and assign the `new_experiment` to it. Also duplicate all EventActions"""
        new_instance = super().create_new_version(save=False, is_copy=is_copy)
        new_instance.experiment = new_experiment
        new_instance.action = new_instance.action.create_new_version(is_copy=is_copy)
        new_instance.save()
        return new_instance

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
                experiment=self.experiment.get_working_version(),
                ended_at=None,
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .annotate(
                last_human_message_created_at=Subquery(last_human_message_created_at),
                log_count=Subquery(log_count_for_last_message),
                failure_count=Subquery(failure_count_for_last_message),
            )
            .filter(
                last_human_message_created_at__gte=self.updated_at,
                # last message received after trigger config was updated
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

        working_version = self.get_working_version()
        try:
            result = ACTION_HANDLERS[self.action.action_type]().invoke(session, self.action)
            working_version.event_logs.create(
                session=session, chat_message=last_human_message, status=EventLogStatusChoices.SUCCESS, log=result
            )
        except Exception as e:
            working_version.event_logs.create(
                session=session, chat_message=last_human_message, status=EventLogStatusChoices.FAILURE, log=str(e)
            )

        if not self._has_triggers_left(working_version, session, last_human_message):
            from apps.events.tasks import enqueue_static_triggers

            enqueue_static_triggers.delay(session.id, StaticTriggerType.LAST_TIMEOUT)

        return result

    def _has_triggers_left(self, working_version, session, message):
        has_succeeded = (
            working_version.event_logs.filter(
                session=session,
                chat_message=message,
                status=EventLogStatusChoices.SUCCESS,
            ).count()
            >= self.total_num_triggers
        )
        failed = (
            working_version.event_logs.filter(
                session=session,
                chat_message=message,
                status=EventLogStatusChoices.FAILURE,
            ).count()
            >= TOTAL_FAILURES
        )

        return not (has_succeeded or failed)

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["action", "experiment", "event_logs"]

    @property
    def version_details(self) -> VersionDetails:
        event_action_type = EventActionType(self.action.action_type).label
        group_name = event_action_type

        action_param_versions = [VersionField(group_name=group_name, name="action", raw_value=event_action_type)]
        for name, value in self.action.params.items():
            action_param_versions.append(VersionField(group_name=group_name, name=name, raw_value=value))

        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name=group_name, name="delay", raw_value=self.delay),
                VersionField(group_name=group_name, name="total_num_triggers", raw_value=self.total_num_triggers),
                *action_param_versions,
            ],
        )


class ScheduledMessageManager(models.Manager):
    def get_messages_to_fire(self):
        return (
            self.filter(is_complete=False, cancelled_at=None, next_trigger_date__lte=functions.Now())
            .select_related("action")
            .order_by("next_trigger_date")
        )


class TimePeriod(models.TextChoices):
    MINUTES = ("minutes", "Minutes")
    HOURS = ("hours", "Hours")
    DAYS = ("days", "Days")
    WEEKS = ("weeks", "Weeks")
    MONTHS = ("months", "Months")


class ScheduledMessage(BaseTeamModel):
    # this only has to be unique per experiment / participant combination
    external_id = models.CharField(max_length=32, help_text="A unique identifier for the scheduled message")
    action = models.ForeignKey(
        EventAction, on_delete=models.CASCADE, related_name="scheduled_messages", null=True, blank=True, default=None
    )
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="scheduled_messages")
    participant = models.ForeignKey(
        "experiments.Participant", on_delete=models.CASCADE, related_name="schduled_messages"
    )
    next_trigger_date = models.DateTimeField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    total_triggers = models.IntegerField(default=0)
    is_complete = models.BooleanField(default=False)
    custom_schedule_params = models.JSONField(blank=True, default=dict)
    end_date = models.DateTimeField(null=True, blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey("users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True)

    objects = ScheduledMessageManager()

    class Meta:
        unique_together = ("experiment", "participant", "external_id")
        indexes = [models.Index(fields=["is_complete"])]

    def save(self, *args, **kwargs):
        self.assign_external_id()
        if not self.next_trigger_date:
            params = self.params
            delta = relativedelta(**{params["time_period"]: params["frequency"]})
            self.next_trigger_date = timezone.now() + delta
        super().save(*args, **kwargs)

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    def cancel(self, cancelled_by=None):
        self.cancelled_at = timezone.now()
        self.cancelled_by = cancelled_by
        self.save()

    def assign_external_id(self):
        if not self.external_id:
            self.external_id = self.generate_external_id(self.name, self.experiment.id, self.participant.id)

    @staticmethod
    def generate_external_id(name: str, experiment_id: int, participant_id: int, instance=None):
        inputs = [name, experiment_id, participant_id]
        return get_next_unique_id(ScheduledMessage, inputs, "external_id", length=5, model_instance=instance)

    def safe_trigger(self):
        """This wraps a call to the _trigger method in a try-catch block"""
        try:
            self._trigger()
        except Exception as e:
            logger.exception(f"An error occurred while trying to send scheduled message {self.id}. Error: {e}")

    def _trigger(self):
        experiment_session = self.participant.get_latest_session(experiment=self.experiment)
        if not experiment_session:
            # Schedules probably created by the API
            return

        trace_info = TraceInfo(
            name="scheduled message",
            metadata={
                "schedule_id": self.external_id,
                "trigger_number": self.total_triggers,
            },
        )
        experiment_session.ad_hoc_bot_message(
            self.params["prompt_text"],
            trace_info,
            fail_silently=False,
            use_experiment=self._get_experiment_to_generate_response(),
        )

        utc_now = timezone.now()
        self.last_triggered_at = utc_now
        self.total_triggers += 1
        if self._should_mark_complete():
            self.is_complete = True
        else:
            delta = relativedelta(**{self.params["time_period"]: self.params["frequency"]})
            self.next_trigger_date = utc_now + delta

        self.save()

    def _get_experiment_to_generate_response(self) -> Experiment:
        """
        - If no child bot was specified to generate the response, use the default experiment version
        - If a child bot was specified to generate the response, we must find the version of the child bot that is
            linked to the default router.
        """
        default_router_experiment = self.experiment.default_version
        experiment_id = self.params.get("experiment_id")
        if experiment_id and int(experiment_id) != self.experiment.id:
            if default_router_experiment.is_a_version and default_router_experiment.child_links.count() > 0:
                # Find the child of this version that has the specified experiment as its working version
                return (
                    default_router_experiment.child_links.filter(child__working_version_id=experiment_id).first().child
                )

            return Experiment.objects.get(id=experiment_id)

        return default_router_experiment

    def _should_mark_complete(self):
        return bool(not self.remaining_triggers or (self.end_date and self.end_date <= timezone.now()))

    @cached_property
    def params(self):
        if self.custom_schedule_params:
            return self.custom_schedule_params

        if not self.action:
            return {}

        # use the latest version of the params
        return self.published_action.params

    @cached_property
    def published_action(self):
        """The action associated with the published version of the trigger that created this message"""
        if not self.action:
            return None

        try:
            trigger = self.action.static_trigger
            trigger = StaticTrigger.objects.get_published_version(trigger)
            return trigger.action
        except StaticTrigger.DoesNotExist:
            pass

        try:
            trigger = self.action.timeout_trigger
            trigger = TimeoutTrigger.objects.get_published_version(trigger)
            return trigger.action
        except TimeoutTrigger.DoesNotExist:
            pass

        return self.action

    @property
    def name(self) -> str:
        return self.params["name"]

    @property
    def frequency(self) -> int:
        return self.params["frequency"]

    @property
    def time_period(self) -> str:
        return self.params["time_period"]

    @property
    def repetitions(self) -> int:
        return self.params["repetitions"] or 0

    @property
    def prompt_text(self) -> str:
        return self.params["prompt_text"]

    @property
    def expected_trigger_count(self):
        return self.repetitions or 1

    @property
    def remaining_triggers(self):
        remaining = self.expected_trigger_count - self.total_triggers
        return max(remaining, 0)

    @property
    def was_created_by_system(self) -> bool:
        return self.action_id is not None

    def as_string(self, as_timezone: str | None = None):
        header_str = f"{self.name} (Message id={self.external_id}, message={self.prompt_text})"
        if self.repetitions <= 1:
            schedule_details_str = "One-off reminder"
        else:
            if self.time_period == TimePeriod.WEEKS:
                weekday = self.next_trigger_date.strftime("%A")
                schedule_details_str = (
                    f"Every {self.frequency} {self.time_period} on {weekday}, {self.repetitions} times"
                )
            else:
                schedule_details_str = f"Every {self.frequency} {self.time_period}, {self.repetitions} times"

        if not self.is_complete and self.remaining_triggers:
            next_trigger_date = pretty_date(self.next_trigger_date, as_timezone=as_timezone)
            next_trigger_str = f"Next trigger is at {next_trigger_date}"
        else:
            next_trigger_str = "Complete"

        tail_str = ""
        if self.action is not None:
            tail_str = " (System)"

        return f"{header_str}: {schedule_details_str}. {next_trigger_str}.{tail_str}"

    def __str__(self):
        return self.as_string()

    def as_dict(self, as_timezone: str = None):
        next_trigger_date = self.next_trigger_date
        last_triggered_at = self.last_triggered_at
        if as_timezone:
            try:
                pytz_timezone = pytz.timezone(as_timezone)
            except UnknownTimeZoneError:
                pass
            else:
                next_trigger_date = next_trigger_date.astimezone(pytz_timezone)
                if last_triggered_at:
                    last_triggered_at = last_triggered_at.astimezone(pytz_timezone)
        return {
            "name": self.name,
            "prompt": self.prompt_text,
            "external_id": self.external_id,
            "frequency": self.frequency,
            "time_period": self.time_period,
            "repetitions": self.repetitions,
            "next_trigger_date": next_trigger_date,
            "last_triggered_at": last_triggered_at,
            "total_triggers": self.total_triggers,
            "triggers_remaining": self.remaining_triggers,
            "is_complete": self.is_complete,
            "is_cancelled": self.is_cancelled,
        }
