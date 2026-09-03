from datetime import date, datetime, timedelta
from datetime import time as dt_time

import pytest
import pytz
from django.test import override_settings
from django.utils import timezone
from time_machine import travel

from apps.events.event_log import EventLogStatusChoices
from apps.events.forms import ScheduledTriggerForm
from apps.events.models import EventAction, EventActionType
from apps.events.scheduled_trigger import scheduled_datetime_for
from apps.events.tasks import fire_scheduled_trigger, poll_due_scheduled_triggers
from apps.experiments.models import VersionFieldDisplayFormatters
from apps.utils.factories.events import EventActionFactory, ScheduledTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory

FUTURE_LOCAL = timezone.now() + timedelta(days=1)


def _trigger(**kwargs):
    defaults = {
        "trigger_date": FUTURE_LOCAL.date(),
        "trigger_time": dt_time(23, 59),
        "timezone": "UTC",
        "action": EventActionFactory(action_type=EventActionType.LOG),
    }
    defaults.update(kwargs)
    return ScheduledTriggerFactory(**defaults)


@pytest.fixture()
def session(team_with_users):
    experiment = ExperimentFactory.create(team=team_with_users)
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.mark.django_db()
class TestScheduledTriggerModel:
    def test_delete_cascades_to_event_action(self):
        trigger = _trigger()
        action_id = trigger.action_id
        trigger.delete()
        assert not EventAction.objects.filter(id=action_id).exists()

    def test_save_clears_fired_at_when_schedule_changes(self, team_with_users):
        trigger = _trigger(experiment=ExperimentFactory.create(team=team_with_users))
        trigger.fired_at = timezone.now()
        trigger.save(update_fields=["fired_at"])
        trigger.refresh_from_db()
        assert trigger.fired_at is not None
        # Change the schedule — fired_at should be cleared
        trigger.trigger_time = dt_time(23, 58)
        trigger.save()
        trigger.refresh_from_db()
        assert trigger.fired_at is None

    def test_scheduled_datetime_is_converted_to_utc_from_configured_timezone(self):
        trigger = _trigger(trigger_time=dt_time(9, 0), timezone="Africa/Lagos")
        assert trigger.scheduled_datetime == scheduled_datetime_for(
            trigger.trigger_date, trigger.trigger_time, trigger.timezone
        )
        assert trigger.scheduled_at == trigger.scheduled_datetime

    def test_non_existent_local_time_advances_through_dst_gap(self):
        gap_date = date(2026, 3, 8)
        gap_time = dt_time(2, 30)
        scheduled = scheduled_datetime_for(gap_date, gap_time, "America/New_York")
        assert scheduled == datetime(2026, 3, 8, 7, 30, tzinfo=pytz.utc)

    def test_format_trigger_displays_scheduled_trigger(self):
        trigger = _trigger(trigger_time=dt_time(9, 0), timezone="Africa/Lagos")
        formatted = VersionFieldDisplayFormatters.format_trigger(trigger)
        assert str(trigger.trigger_date) in formatted
        assert "09:00" in formatted
        assert "Africa/Lagos" in formatted
        assert "then log" in formatted

    def test_fire_sets_fired_at_and_logs_success(self, session):
        trigger = _trigger(experiment=session.experiment)
        trigger.fire()
        trigger.refresh_from_db()
        assert trigger.fired_at is not None
        assert trigger.event_logs.count() == 1
        assert trigger.event_logs.first().status == EventLogStatusChoices.SUCCESS
        assert trigger.event_logs.first().session == session

    def test_fire_does_not_fire_twice_when_called_concurrently(self, session):
        trigger = _trigger(experiment=session.experiment)
        trigger.fire()
        trigger.fire()
        trigger.refresh_from_db()
        assert trigger.event_logs.count() == 1

    def test_inactive_trigger_is_not_fired(self, session):
        trigger = _trigger(experiment=session.experiment, is_active=False)
        trigger.fire()
        trigger.refresh_from_db()
        assert trigger.fired_at is None
        assert trigger.event_logs.count() == 0

    def test_fire_with_no_session_logs_failure_gracefully(self):
        experiment = ExperimentFactory.create()
        trigger = _trigger(experiment=experiment)
        trigger.fire()
        trigger.refresh_from_db()
        assert trigger.fired_at is not None
        assert trigger.event_logs.count() == 1
        assert trigger.event_logs.first().status == EventLogStatusChoices.FAILURE
        assert trigger.event_logs.first().session is None

    def test_fire_resolves_session_from_published_experiment(self, team_with_users):
        # Sessions belong to the published experiment; the trigger must fire against it,
        # not against get_working_version() which has no sessions.
        working = ExperimentFactory.create(team=team_with_users)
        published = working.create_new_version(make_default=True)
        session = ExperimentSessionFactory.create(experiment=published)
        trigger = _trigger(experiment=published)
        trigger.fire()
        trigger.refresh_from_db()
        assert trigger.fired_at is not None
        log = trigger.event_logs.first()
        assert log.status == EventLogStatusChoices.SUCCESS
        assert log.session == session


@pytest.mark.django_db()
class TestScheduledTriggerCelery:
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_poll_enqueues_due_active_triggers(self, team_with_users):
        working = ExperimentFactory.create(team=team_with_users)
        published = working.create_new_version(make_default=True)
        past_date = (timezone.now() + timedelta(days=1)).date()
        past_time = dt_time(0, 0)
        due = _trigger(experiment=published, trigger_date=past_date, trigger_time=past_time, timezone="UTC")
        inactive = _trigger(
            experiment=published, trigger_date=past_date, trigger_time=past_time, timezone="UTC", is_active=False
        )
        future = _trigger(experiment=published, timezone="UTC")

        with travel(timezone.now() + timedelta(days=1, hours=2), tick=False):
            poll_due_scheduled_triggers()

        due.refresh_from_db()
        assert due.fired_at is not None
        assert due.event_logs.count() == 1
        inactive.refresh_from_db()
        assert inactive.fired_at is None
        future.refresh_from_db()
        assert future.fired_at is None

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_poll_skips_archived_due_triggers(self, team_with_users):
        working = ExperimentFactory.create(team=team_with_users)
        published = working.create_new_version(make_default=True)
        past_date = (timezone.now() + timedelta(days=1)).date()
        past_time = dt_time(0, 0)
        archived = _trigger(experiment=published, trigger_date=past_date, trigger_time=past_time, timezone="UTC")
        archived.archive()

        with travel(timezone.now() + timedelta(days=1, hours=2), tick=False):
            poll_due_scheduled_triggers()

        archived.refresh_from_db()
        assert archived.fired_at is None

    def test_fire_scheduled_trigger_task(self, session):
        trigger = _trigger(experiment=session.experiment)
        fire_scheduled_trigger(trigger.id)
        trigger.refresh_from_db()
        assert trigger.fired_at is not None


@pytest.mark.django_db()
class TestScheduledTriggerForm:
    def test_past_datetime_is_rejected(self, team_with_users):
        past_date = (timezone.now() - timedelta(days=1)).date()
        form = ScheduledTriggerForm(
            data={"trigger_date": past_date.isoformat(), "trigger_time": "09:00", "timezone": "UTC"}
        )
        assert not form.is_valid()
        assert "The scheduled time must be in the future." in form.errors.get("trigger_date", [])

    def test_future_datetime_is_accepted(self, team_with_users):
        future_date = (timezone.now() + timedelta(days=1)).date()
        form = ScheduledTriggerForm(
            data={"trigger_date": future_date.isoformat(), "trigger_time": "09:00", "timezone": "UTC"}
        )
        assert form.is_valid()

    def test_edit_with_unchanged_past_schedule_is_accepted(self, team_with_users):
        # A trigger scheduled in the past should still be editable if only non-schedule fields change.
        trigger = _trigger(experiment=ExperimentFactory.create(team=team_with_users))
        past_date = (timezone.now() - timedelta(days=1)).date()
        trigger.trigger_date = past_date
        trigger.save(update_fields=["trigger_date", "scheduled_at"])
        trigger.refresh_from_db()
        form = ScheduledTriggerForm(
            instance=trigger,
            data={
                "trigger_date": past_date.isoformat(),
                "trigger_time": trigger.trigger_time.strftime("%H:%M"),
                "timezone": trigger.timezone,
            },
        )
        assert form.is_valid(), form.errors


@pytest.mark.django_db()
class TestScheduledTriggerArchive:
    def test_archive_archives_scheduled_triggers(self, team_with_users):
        experiment = ExperimentFactory.create(team=team_with_users)
        trigger = _trigger(experiment=experiment)
        experiment.archive()
        trigger.refresh_from_db()
        assert trigger.is_archived is True

    def test_unarchive_restores_scheduled_triggers(self, team_with_users):
        experiment = ExperimentFactory.create(team=team_with_users)
        trigger = _trigger(experiment=experiment)
        experiment.archive()
        experiment.unarchive()
        trigger.refresh_from_db()
        assert trigger.is_archived is False
