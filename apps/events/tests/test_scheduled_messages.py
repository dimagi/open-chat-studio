import logging
from datetime import datetime
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from freezegun import freeze_time

from apps.events.models import EventActionType, ScheduledMessage, TimePeriod
from apps.events.tasks import _get_messages_to_fire, poll_scheduled_messages
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.time import timedelta_to_relative_delta


def _construct_event_action(time_period: TimePeriod, experiment_id: int, frequency=1, repetitions=1) -> tuple:
    params = {
        "name": "Test",
        "time_period": time_period,
        "frequency": frequency,
        "repetitions": repetitions,
        "prompt_text": "",
        "experiment_id": experiment_id,
    }
    return EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER), params


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["hours", "days", "weeks"])
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_create_scheduled_message_sets_start_date(ad_hoc_bot_message, period):
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(time_period=TimePeriod(period), experiment_id=session.experiment.id)
    with freeze_time("2024-01-01"):
        message = ScheduledMessage.objects.create(
            participant=session.participant, team=session.team, action=event_action, experiment=session.experiment
        )
        delta = relativedelta(**{params["time_period"]: params["frequency"]})
        rel_delta = timedelta_to_relative_delta(message.next_trigger_date - timezone.now())
        assert rel_delta == delta


@pytest.mark.django_db()
def test_get_messages_to_fire():
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
    )
    with freeze_time("2024-04-01"), patch("apps.chat.tasks.functions.Now") as db_time:
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
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod(period), repetitions=2, experiment_id=session.experiment.id
    )
    delta = relativedelta(**{params["time_period"]: params["frequency"]})
    seconds_offset = 1
    step_delta = delta + relativedelta(seconds=seconds_offset)

    with freeze_time("2024-04-01") as frozen_time, patch("apps.chat.tasks.functions.Now") as db_time:
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


@pytest.mark.django_db()
@patch("apps.channels.forms.TelegramChannelForm._set_telegram_webhook")
def test_error_when_sending_sending_message_to_a_user(_set_telegram_webhook, caplog):
    """This test makes sure that any error that happens when sending a message to a user does not affect other
    pending messages"""

    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, repetitions=2, experiment_id=session.experiment.id
    )
    with (
        caplog.at_level(logging.ERROR),
        patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message", side_effect=Exception("Oops")),
        patch("apps.chat.tasks.functions.Now") as db_time,
    ):
        sm = ScheduledMessageFactory(
            participant=session.participant, action=event_action, team=session.team, experiment=session.experiment
        )

        # Let's put the DB time ahead of the scheduled message
        utc_now = timezone.now()
        db_time.return_value = utc_now + relativedelta(days=1.1)

        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 1

        poll_scheduled_messages()
        assert len(caplog.records) == 1
        expected_msg = f"An error occured while trying to send scheduled messsage {sm.id}. Error: Oops"
        assert caplog.records[0].msg == expected_msg

        assert sm.last_triggered_at is None


def _assert_next_trigger_date(message: ScheduledMessage, expected_date: datetime):
    message.refresh_from_db()
    assert message.next_trigger_date == expected_date


@pytest.mark.django_db()
def test_schedule_update():
    """Tests that a frequency update affects scheduled messages in the following way:
    if last_triggered_at is None, set next_trigger_date as created_at + delta
    if last_triggered_at is not None, set next_trigger_date as last_triggered_at + delta
    """
    session = ExperimentSessionFactory()
    experiment = session.experiment
    team = experiment.team
    session2 = ExperimentSessionFactory(team=team, experiment=experiment)
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.WEEKS, repetitions=4, experiment_id=session.experiment.id
    )

    message1 = ScheduledMessage.objects.create(
        participant=session.participant, team=session.team, action=event_action, experiment=session.experiment
    )
    message2 = ScheduledMessage.objects.create(
        participant=session2.participant,
        team=session.team,
        action=event_action,
        last_triggered_at=timezone.now() - relativedelta(days=5),
        experiment=session2.experiment,
    )
    message3 = ScheduledMessage.objects.create(
        participant=session2.participant,
        team=session.team,
        action=event_action,
        last_triggered_at=timezone.now() - relativedelta(days=1),
        is_complete=True,
        experiment=session2.experiment,
    )
    message3_next_trigger_data = message3.next_trigger_date

    message1_prev_trigger_date = message1.next_trigger_date
    message2_prev_trigger_date = message2.next_trigger_date

    # Frequency update. Message1 should use its `created_at` as the baseline, message2 its `last_triggered_at`
    new_frequency = 2
    new_delta = relativedelta(**{params["time_period"]: new_frequency})
    event_action.params["frequency"] = new_frequency
    event_action.save()

    _assert_next_trigger_date(message1, message1.created_at + new_delta)
    _assert_next_trigger_date(message2, message2.last_triggered_at + new_delta)
    # Since message3 is completed, its `next_trigger_date` and `is_complete` should not change
    _assert_next_trigger_date(message3, message3_next_trigger_data)
    assert message3.is_complete is True
    assert message1.next_trigger_date > message1_prev_trigger_date
    assert message2.next_trigger_date > message2_prev_trigger_date
    message1_prev_trigger_date = message1.next_trigger_date
    message2_prev_trigger_date = message2.next_trigger_date

    # Time period update. Message1 should use its `created_at` as the baseline, message2 its `last_triggered_at`
    new_period = TimePeriod.DAYS
    event_action.params["time_period"] = new_period
    new_delta = relativedelta(**{new_period: event_action.params["frequency"]})
    event_action.save()

    _assert_next_trigger_date(message1, message1.created_at + new_delta)
    _assert_next_trigger_date(message2, message2.last_triggered_at + new_delta)
    _assert_next_trigger_date(message3, message3_next_trigger_data)
    assert message1.next_trigger_date < message1_prev_trigger_date
    assert message2.next_trigger_date < message2_prev_trigger_date
