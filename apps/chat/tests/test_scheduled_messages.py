from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import pytz
from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from apps.chat.models import ScheduledMessage, ScheduledMessageConfig, TimePeriod, TriggerEvent
from apps.chat.tasks import _get_messages_to_fire, poll_scheduled_messages
from apps.utils.factories.chat import ScheduledMessageConfigFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.time import timedelta_to_relative_delta


def test_validation_error_raised(experiment):
    data = {
        "name": "pesky reminder",
        "team": experiment.team,
        "experiment": experiment,
        "trigger_event": TriggerEvent.CONVERSATION_START,
        "recurring": True,
        "time_period": TimePeriod.WEEKS,
        "frequency": 2,
        "repetitions": 0,
        "prompt_text": "Check in with the user",
    }
    with pytest.raises(ValueError, match="Recurring schedules require `repetitions` to be larger than 0"):
        ScheduledMessageConfig.objects.create(**data)

    data["recurring"] = False
    data["repetitions"] = 2
    with pytest.raises(ValueError, match="Non recurring schedules cannot have `repetitions` larger than 0"):
        ScheduledMessageConfig.objects.create(**data)


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["hours"])
@patch("apps.experiments.models.ExperimentSession.send_bot_message")
def test_create_scheduled_message_sets_start_date(send_bot_message, period):
    session = ExperimentSessionFactory()
    experiment = session.experiment
    team = experiment.team
    schedule_conf = ScheduledMessageConfigFactory(
        experiment=experiment,
        team=team,
        time_period=TimePeriod(period),
        frequency=1,
    )
    with freeze_time("2024-01-01"):
        message = ScheduledMessage.objects.create(
            participant=session.participant, team=session.team, schedule=schedule_conf
        )
        delta = relativedelta(**{schedule_conf.time_period: schedule_conf.frequency})
        utc_now = datetime.now().astimezone(pytz.timezone("UTC"))
        rel_delta = timedelta_to_relative_delta(message.next_trigger_date - utc_now)
        assert rel_delta == delta


@pytest.mark.django_db()
def test_get_messages_to_fire():
    session = ExperimentSessionFactory()
    experiment = session.experiment
    team = experiment.team
    schedule_conf = ScheduledMessageConfigFactory(
        experiment=experiment,
        team=team,
        time_period=TimePeriod.DAYS,
        frequency=1,
        repetitions=2,
    )
    with freeze_time("2024-04-01"), patch("apps.chat.tasks.functions.Now") as db_time:
        utc_now = datetime.now().astimezone(pytz.timezone("UTC"))
        db_time.return_value = utc_now

        scheduled_message = ScheduledMessageFactory(participant=session.participant, schedule=schedule_conf)
        # DB is behind the trigger date
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 0

        # DB is now ahead of the trigger date
        db_time.return_value = utc_now + relativedelta(days=1.1)
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 1
        assert pending_messages[0] == scheduled_message

        scheduled_message.resolved = True
        scheduled_message.save()

        # Resolved messages should not be returned
        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 0


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["hours", "days", "weeks", "months"])
@patch("apps.experiments.models.ExperimentSession.send_bot_message")
def test_poll_scheduled_messages(send_bot_message, period):
    scheduled_message = None
    delta = None

    def step_time(frozen_time, db_time, timedelta):
        """Step time"""
        frozen_time.tick(delta=timedelta)
        now = datetime.now().astimezone(pytz.timezone("UTC"))
        db_time.return_value = now
        return now

    def test_scheduled_message_attrs(
        expected_next_trigger_date,
        expected_last_triggered_at,
        expected_total_triggers,
        expected_resolved,
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
        assert scheduled_message.resolved == expected_resolved

    session = ExperimentSessionFactory()
    experiment = session.experiment
    team = experiment.team
    schedule_conf = ScheduledMessageConfigFactory(
        experiment=experiment,
        team=team,
        time_period=TimePeriod(period),
        frequency=1,
        repetitions=2,
    )
    delta = relativedelta(**{schedule_conf.time_period: schedule_conf.frequency})
    seconds_offset = 1
    step_delta = delta + relativedelta(seconds=seconds_offset)

    with freeze_time("2024-04-01") as frozen_time, patch("apps.chat.tasks.functions.Now") as db_time:
        current_time = db_time.return_value = datetime.now().astimezone(pytz.timezone("UTC"))
        scheduled_message = ScheduledMessageFactory(participant=session.participant, schedule=schedule_conf)
        # Set the DB time to now

        # DB is now behind the next trigger date
        poll_scheduled_messages()
        test_scheduled_message_attrs(
            expected_next_trigger_date=current_time + delta,
            expected_last_triggered_at=None,
            expected_total_triggers=0,
            expected_resolved=False,
            expect_next_trigger_date_changed=False,
        )

        current_time = step_time(frozen_time, db_time, step_delta)
        poll_scheduled_messages()
        test_scheduled_message_attrs(
            expected_next_trigger_date=current_time + delta,
            expected_last_triggered_at=current_time,
            expected_total_triggers=1,
            expected_resolved=False,
        )

        current_time = step_time(frozen_time, db_time, step_delta)
        poll_scheduled_messages()
        # Since the scheduled message is be resolved, the expected_next_trigger_date will not be updated
        test_scheduled_message_attrs(
            # We subtract the offset here to account for it not being in the original `next_trigger_date`
            expected_next_trigger_date=current_time - relativedelta(seconds=seconds_offset),
            expected_last_triggered_at=current_time,
            expected_total_triggers=2,
            expected_resolved=True,
            expect_next_trigger_date_changed=False,
        )

        # We are done now, but let's make 110% sure that another message will not be triggered
        current_time = step_time(frozen_time, db_time, step_delta)
        scheduled_message._trigger = Mock()
        poll_scheduled_messages()
        scheduled_message._trigger.assert_not_called()


@pytest.mark.django_db()
def test_error_when_sending_sending_message_to_a_user(caplog):
    """This test makes sure that any error that happens when sending a message to a user does not affect other
    pending messages"""

    session = ExperimentSessionFactory()
    experiment = session.experiment
    team = experiment.team
    schedule_conf = ScheduledMessageConfigFactory(
        experiment=experiment,
        team=team,
        time_period=TimePeriod.DAYS,
        frequency=1,
        repetitions=2,
    )
    with (
        caplog.at_level(logging.ERROR),
        patch("apps.experiments.models.ExperimentSession.send_bot_message", side_effect=Exception("Oops")),
        patch("apps.chat.tasks.functions.Now") as db_time,
    ):
        sm = ScheduledMessageFactory(participant=session.participant, schedule=schedule_conf)

        # Let's put the DB time ahead of the scheduled message
        utc_now = datetime.now().astimezone(pytz.timezone("UTC"))
        db_time.return_value = utc_now + relativedelta(days=1.1)

        pending_messages = _get_messages_to_fire()
        assert len(pending_messages) == 1

        poll_scheduled_messages()
        assert len(caplog.records) == 1
        for record in caplog.records:
            record

        assert sm.last_triggered_at is None
