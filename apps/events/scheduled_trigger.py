"""Scheduled-trigger model, manager, and DST-aware UTC conversion helpers."""

import logging
from datetime import datetime, timedelta

import pytz
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models, transaction
from django.utils import timezone
from pytz.exceptions import NonExistentTimeError

from apps.events.event_log import EventLog, EventLogStatusChoices
from apps.events.models import ACTION_HANDLERS, EventAction, EventActionType
from apps.experiments.models import Experiment
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.utils.models import BaseModel

logger = logging.getLogger("ocs.events")


def scheduled_datetime_for(trigger_date, trigger_time, timezone_name) -> datetime:
    """Convert a naive local date/time in ``timezone_name`` into an aware UTC datetime.

    Ambiguous or non-existent local times (DST transitions) are resolved deterministically:
    an ambiguous time maps to the standard-time occurrence, and a non-existent time is
    advanced through the spring-forward gap to preserve the intended wall-clock schedule.
    """
    tz = pytz.timezone(timezone_name)
    local_datetime = datetime.combine(trigger_date, trigger_time)
    try:
        localized = tz.localize(local_datetime, is_dst=False)
    except NonExistentTimeError:
        localized = tz.localize(_advance_through_dst_gap(tz, local_datetime))
    return localized.astimezone(pytz.utc)


def _advance_through_dst_gap(tz, local_datetime) -> datetime:
    """Advance a naive local time that falls in a spring-forward gap over that gap.

    The gap equals the jump in UTC offset across the transition (typically one hour), so
    adding it yields a valid local instant just after the gap, preserving the wall-clock slot.
    """
    offset_before = tz.localize(local_datetime - timedelta(hours=1)).utcoffset() or timedelta()
    offset_after = tz.localize(local_datetime + timedelta(hours=1)).utcoffset() or timedelta()
    return local_datetime + (offset_after - offset_before)


class ScheduledTriggerObjectManager(VersionsObjectManagerMixin, models.Manager):
    def published_versions(self):
        return self.filter(experiment__is_default_version=True)

    def get_published_version(self, trigger):
        return self.published_versions().get(working_version_id=trigger.get_working_version_id())


class ScheduledTrigger(BaseModel, VersionsMixin):
    action = models.OneToOneField(EventAction, on_delete=models.CASCADE, related_name="scheduled_trigger")
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="scheduled_triggers")
    trigger_date = models.DateField(help_text="The date on which the trigger should fire (YYYY-MM-DD)")
    trigger_time = models.TimeField(help_text="The local time at which the trigger should fire (HH:MM)")
    timezone = models.CharField(max_length=64, help_text="IANA timezone name in which date/time are interpreted")
    scheduled_at = models.DateTimeField(
        help_text="The trigger moment converted to UTC at save time, used to determine when the trigger is due"
    )
    fired_at = models.DateTimeField(null=True, blank=True)
    event_logs = GenericRelation(EventLog)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = ScheduledTriggerObjectManager()
    is_active = models.BooleanField(default=True)

    @property
    def trigger_type(self):
        return "ScheduledTrigger"

    @property
    def scheduled_datetime(self):
        """The trigger moment expressed as an aware UTC datetime."""
        return scheduled_datetime_for(self.trigger_date, self.trigger_time, self.timezone)

    def save(self, *args, **kwargs):
        new_scheduled_at = self.scheduled_datetime
        if self.pk and self.scheduled_at != new_scheduled_at:
            # Schedule changed on an existing trigger; allow it to fire again.
            self.fired_at = None
        self.scheduled_at = new_scheduled_at
        super().save(*args, **kwargs)

    @transaction.atomic()
    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self.action.delete(*args, **kwargs)
        return result

    def fire(self):
        working_version = self.get_working_version()
        claimed = ScheduledTrigger.objects.filter(id=self.id, fired_at__isnull=True, is_active=True).update(
            fired_at=timezone.now()
        )
        if not claimed:
            return None

        session = self._resolve_session()
        try:
            if not session:
                raise ValueError("No active session was found to run this trigger.")
            handler_cls = ACTION_HANDLERS.get(self.action.action_type)
            if not handler_cls:
                raise ValueError(f"Action with type '{self.action.action_type}' not found.")
            result = handler_cls().invoke(session, self.action)
            working_version.event_logs.create(session=session, status=EventLogStatusChoices.SUCCESS, log=result)
            return result
        except Exception as e:
            logger.exception(e)
            working_version.event_logs.create(session=session, status=EventLogStatusChoices.FAILURE, log=str(e))
        return None

    def _resolve_session(self):
        return self.experiment.sessions.order_by("-created_at").first()

    @transaction.atomic()
    def create_new_version(self, new_experiment: Experiment, is_copy: bool = False):  # ty: ignore[invalid-method-override]
        new_instance = super().create_new_version(save=False, is_copy=is_copy)
        new_instance.experiment = new_experiment
        new_instance.action = new_instance.action.create_new_version(is_copy=is_copy)
        new_instance.fired_at = None
        new_instance.save()
        return new_instance

    def get_fields_to_exclude(self):
        return super().get_fields_to_exclude() + ["action", "experiment", "event_logs", "fired_at"]

    def _get_version_details(self):
        event_action_type = EventActionType(self.action.action_type).label
        action_param_versions = [VersionField(group_name=event_action_type, name="action", raw_value=event_action_type)]
        for name, value in self.action.params.items():
            action_param_versions.append(VersionField(group_name=event_action_type, name=name, raw_value=value))

        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="Schedule", name="date", raw_value=self.trigger_date),
                VersionField(group_name="Schedule", name="time", raw_value=self.trigger_time),
                VersionField(group_name="Schedule", name="timezone", raw_value=self.timezone),
                *action_param_versions,
            ],
        )
