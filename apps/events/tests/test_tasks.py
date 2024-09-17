from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from freezegun import freeze_time

from apps.events.models import TimePeriod
from apps.events.tasks import _get_messages_to_fire, poll_scheduled_messages
from apps.events.tests.utils import construct_event_action
from apps.utils.factories.events import ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_get_messages_to_fire():
    session = ExperimentSessionFactory()
    event_action, params = construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
    )
    with freeze_time("2024-04-01"), patch("apps.events.tasks.functions.Now") as db_time:
        utc_now = timezone.now()
        db_time.return_value = utc_now

        scheduled_message = ScheduledMessageFactory(
            team=session.team, participant=session.participant, action=event_action
        )
        # DB is behind the trigger date
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 0

        # DB is now ahead of the trigger date
        db_time.return_value = utc_now + relativedelta(days=1.1)
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 1
        assert pending_messages[0] == scheduled_message

        scheduled_message.is_complete = True
        scheduled_message.save()

        # Completed messages should not be returned
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 0


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["hours", "days", "weeks", "months"])
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_poll_scheduled_messages(ad_hoc_bot_message, period):
    scheduled_message = None
    delta = None

    def step_time(frozen_time, db_time, timedelta):
        """Step time"""
        frozen_time.tick(delta=timedelta)
        now = timezone.now()
        db_time.return_value = now
        return now

    def test_scheduled_message_attrs(
        expected_next_trigger_date,
        expected_last_triggered_at,
        expected_total_triggers,
        expected_is_complete,
        expect_next_trigger_date_changed=True,
    ):
        prev_next_trigger_date = scheduled_message.next_trigger_date
        scheduled_message.refresh_from_db()
        if expect_next_trigger_date_changed:
            # Assert that the date actually moved on
            assert scheduled_message.next_trigger_date > prev_next_trigger_date

        assert scheduled_message.next_trigger_date == expected_next_trigger_date
        assert scheduled_message.last_triggered_at == expected_last_triggered_at
        assert scheduled_message.total_triggers == expected_total_triggers
        assert scheduled_message.is_complete == expected_is_complete

    session = ExperimentSessionFactory()
    event_action, params = construct_event_action(
        frequency=1, time_period=TimePeriod(period), repetitions=2, experiment_id=session.experiment.id
    )
    delta = relativedelta(**{params["time_period"]: params["frequency"]})
    seconds_offset = 1
    step_delta = delta + relativedelta(seconds=seconds_offset)

    with freeze_time("2024-04-01") as frozen_time, patch("apps.events.tasks.functions.Now") as db_time:
        current_time = db_time.return_value = timezone.now()
        scheduled_message = ScheduledMessageFactory(
            team=session.team, participant=session.participant, action=event_action, experiment=session.experiment
        )
        # Set the DB time to now

        # DB is now behind the next trigger date
        poll_scheduled_messages()
        test_scheduled_message_attrs(
            expected_next_trigger_date=current_time + delta,
            expected_last_triggered_at=None,
            expected_total_triggers=0,
            expected_is_complete=False,
            expect_next_trigger_date_changed=False,
        )

        current_time = step_time(frozen_time, db_time, step_delta)
        poll_scheduled_messages()
        test_scheduled_message_attrs(
            expected_next_trigger_date=current_time + delta,
            expected_last_triggered_at=current_time,
            expected_total_triggers=1,
            expected_is_complete=False,
        )

        current_time = step_time(frozen_time, db_time, step_delta)
        poll_scheduled_messages()
        # Since the scheduled message is be completed, the expected_next_trigger_date will not be updated
        test_scheduled_message_attrs(
            # We subtract the offset here to account for it not being in the original `next_trigger_date`
            expected_next_trigger_date=current_time - relativedelta(seconds=seconds_offset),
            expected_last_triggered_at=current_time,
            expected_total_triggers=2,
            expected_is_complete=True,
            expect_next_trigger_date_changed=False,
        )

        # We are done now, but let's make 110% sure that another message will not be triggered
        current_time = step_time(frozen_time, db_time, step_delta)
        assert len(_get_messages_to_fire()) == 0
