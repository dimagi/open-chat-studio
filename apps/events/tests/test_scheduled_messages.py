import logging
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from time_machine import travel

from apps.events.models import (
    EventActionType,
    EventLogStatusChoices,
    ScheduledMessage,
    ScheduledMessageAttempt,
    StaticTrigger,
    StaticTriggerType,
    TimePeriod,
)
from apps.events.tasks import poll_scheduled_messages, retry_scheduled_message
from apps.experiments.models import ExperimentRoute
from apps.utils.factories.events import EventActionFactory, ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.time import timedelta_to_relative_delta


def _construct_event_action(time_period: TimePeriod, experiment_id: int, frequency=1, repetitions=1) -> tuple:
    params = {
        "name": "Test",
        "time_period": time_period,
        "frequency": frequency,
        "repetitions": repetitions,
        "prompt_text": "hi",
        "experiment_id": experiment_id,
    }
    return EventActionFactory(params=params, action_type=EventActionType.SCHEDULETRIGGER), params


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["hours", "days", "weeks"])
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_create_scheduled_message_sets_start_date_and_external_id(ad_hoc_bot_message, period):
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(time_period=TimePeriod(period), experiment_id=session.experiment.id)
    with travel("2024-01-01", tick=False):
        message = ScheduledMessage.objects.create(
            participant=session.participant, team=session.team, action=event_action, experiment=session.experiment
        )
        delta = relativedelta(**{params["time_period"]: params["frequency"]})
        rel_delta = timedelta_to_relative_delta(message.next_trigger_date - timezone.now())
        assert rel_delta == delta
        assert message.external_id is not None
        assert message.external_id != ""


@pytest.mark.django_db()
def test_get_messages_to_fire():
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
    )
    with travel("2024-04-01", tick=False), patch("apps.events.models.functions.Now") as db_time:
        utc_now = timezone.now()
        db_time.return_value = utc_now

        scheduled_message = ScheduledMessageFactory(
            team=session.team, participant=session.participant, action=event_action
        )
        # behind the trigger date
        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 0

        # ahead of the trigger date
        db_time.return_value = utc_now + relativedelta(days=2)
        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 1
        assert pending_messages[0] == scheduled_message

        scheduled_message.is_complete = True
        scheduled_message.save()

        # Completed messages should not be returned
        db_time.return_value = utc_now + relativedelta(days=4)
        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 0


@pytest.mark.django_db()
def test_get_messages_to_fire_cancelled():
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, experiment_id=session.experiment.id
    )
    with travel("2024-04-01", tick=False), patch("apps.events.models.functions.Now") as db_time:
        utc_now = timezone.now()

        scheduled_message = ScheduledMessageFactory(
            team=session.team, participant=session.participant, action=event_action
        )
        db_time.return_value = utc_now + relativedelta(days=2)
        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 1

        scheduled_message.cancel()

        db_time.return_value = utc_now + relativedelta(days=2)
        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 0


@pytest.mark.django_db()
@pytest.mark.parametrize("period", ["minutes", "hours", "days", "weeks", "months"])
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_poll_scheduled_messages(ad_hoc_bot_message, period):
    ad_hoc_bot_message.return_value = {"trace_id": "abc123", "trace_provider": "langfuse"}
    scheduled_message = None
    delta = None

    def step_time(frozen_time, db_time, delta):
        """Step time"""
        now = timezone.now()
        if isinstance(delta, relativedelta):
            if delta.months:
                new_time = now + delta
                frozen_time.move_to(new_time)
            else:
                td = timedelta(
                    days=delta.days,
                    seconds=delta.seconds,
                    minutes=delta.minutes,
                    hours=delta.hours,
                )
                frozen_time.shift(td)
        else:
            frozen_time.shift(delta)

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

    with travel("2024-04-01", tick=False) as frozen_time, patch("apps.events.models.functions.Now") as db_time:
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
        assert len(ScheduledMessage.objects.get_messages_to_fire()) == 0


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
        patch("apps.events.models.functions.Now") as db_time,
    ):
        sm = ScheduledMessageFactory(
            participant=session.participant, action=event_action, team=session.team, experiment=session.experiment
        )

        # Let's put the DB time ahead of the scheduled message
        utc_now = timezone.now()
        db_time.return_value = utc_now + relativedelta(days=1.1)

        pending_messages = ScheduledMessage.objects.get_messages_to_fire()
        assert len(pending_messages) == 1

        poll_scheduled_messages()
        assert len(caplog.records) == 1
        expected_msg = f"An error occurred while trying to send scheduled message {sm.id}. Error: Oops"
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


@pytest.mark.django_db()
def test_update_schedule_to_minute_period():
    """
    This test reproduces an exception that was raised when the user updates the schedule to use TimePeriod.MINUTES. The
    exception was caused because of a mismatch between the value of `TimePeriod.MINUTES` and that which Postgres expects
    in the MakeInterval function i.e. postgres expects `mins` and the value is `minutes`.
    """
    session = ExperimentSessionFactory()
    event_action, _params = _construct_event_action(
        frequency=1, time_period=TimePeriod.HOURS, repetitions=1, experiment_id=session.experiment.id
    )

    scheduled_message = ScheduledMessage.objects.create(
        participant=session.participant, team=session.team, action=event_action, experiment=session.experiment
    )

    event_action.params["time_period"] = TimePeriod.MINUTES
    # An error was previously thrown when saving
    event_action.save()
    assert scheduled_message.action.params["time_period"] == "minutes"


@pytest.mark.django_db()
def test_schedule_trigger_for_versioned_routes():
    router = ExperimentFactory()
    child = ExperimentFactory(team=router.team)
    session = ExperimentSessionFactory(experiment=router)

    ExperimentRoute.objects.create(
        team=router.team,
        parent=router,
        child=child,
        keyword="keyword1",
    )

    event_action, params = _construct_event_action(frequency=1, time_period=TimePeriod.DAYS, experiment_id=None)
    sm = ScheduledMessageFactory(
        team=router.team, action=event_action, experiment=router, participant=session.participant
    )
    # No experiment specified, so the router should be used (no version yet, so router == default version)
    assert sm._get_experiment_to_generate_response() == router

    new_params = sm.action.params
    new_params["experiment_id"] = child.id
    sm.action.params = new_params
    sm.action.save()

    # No versions yet, so the working version of the child should be used
    assert sm._get_experiment_to_generate_response() == child

    default_router = router.create_new_version(make_default=True)
    router.refresh_from_db()
    del router.default_version
    sm.refresh_from_db()
    child_version = default_router.child_links.first().child
    assert new_params["experiment_id"] == child_version.working_version_id
    # The router is versioned and the deployed version is not the working version, so the child of the deployed version
    # should be used
    assert sm._get_experiment_to_generate_response() == child_version


@pytest.mark.django_db()
def test_action_params_with_versioning():
    """Test that the message params get updated when new versions of the experiment are created."""
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period=TimePeriod.DAYS, repetitions=2, experiment_id=session.experiment.id
    )
    trigger = StaticTrigger.objects.create(
        experiment=session.experiment,
        action=event_action,
        type=StaticTriggerType.CONVERSATION_START,
    )
    trigger.fire(session)

    messages = ScheduledMessage.objects.filter(experiment=session.experiment).all()
    assert len(messages) == 1

    # no versioning yet, so the message should reference the working version
    assert messages[0].params["prompt_text"] == params["prompt_text"]

    event_action.params["prompt_text"] = "hello"
    event_action.save()

    experiment_version = session.experiment.create_new_version(make_default=False)

    # still references working version since there is no default version
    message = ScheduledMessage.objects.get(id=messages[0].id)
    assert message.params["prompt_text"] == params["prompt_text"]

    experiment_version.is_default_version = True
    experiment_version.save()

    # now it should reference the published version
    message = ScheduledMessage.objects.get(id=message.id)
    assert message.params["prompt_text"] == "hello"


@pytest.mark.django_db()
@patch("apps.events.tasks.retry_scheduled_message.apply_async")
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_scheduled_message_attempts_success_and_failure(ad_hoc_bot_message, mock_retry_task):
    """Test ScheduledMessageAttempt creation for both success and failure with retry"""
    ad_hoc_bot_message.return_value = {"trace_id": "abc123", "trace_provider": "langfuse"}
    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period="minutes", repetitions=1, experiment_id=session.experiment.id
    )
    sm = ScheduledMessageFactory(
        participant=session.participant,
        action=event_action,
        team=session.team,
        experiment=session.experiment,
    )
    sm.next_trigger_date = timezone.now() - relativedelta(minutes=1)
    sm.save()
    ad_hoc_bot_message.return_value = None

    poll_scheduled_messages()
    sm.refresh_from_db()

    attempt = ScheduledMessageAttempt.objects.get(scheduled_message=sm)
    assert attempt.trigger_number == 0
    assert attempt.attempt_number == 1
    assert attempt.attempt_result == EventLogStatusChoices.SUCCESS
    assert attempt.trace_info is not None

    ad_hoc_bot_message.side_effect = Exception("Oops")
    retry_scheduled_message(scheduled_message_id=sm.id, attempt_number=2)
    sm.refresh_from_db()

    attempts = ScheduledMessageAttempt.objects.filter(scheduled_message=sm).order_by("attempt_number")
    assert attempts.count() == 2

    fail_attempt = attempts.filter(attempt_result=EventLogStatusChoices.FAILURE).first()
    assert fail_attempt is not None
    assert fail_attempt.attempt_number == 2
    assert fail_attempt.trigger_number == 1
    assert fail_attempt.log_message == "Oops"

    mock_retry_task.assert_called_once()
    args, kwargs = mock_retry_task.call_args
    assert args == ()
    assert kwargs["args"] == [sm.id, 3]
    assert kwargs["countdown"] == 2


@pytest.mark.django_db()
@patch("apps.events.tasks.retry_scheduled_message.apply_async")
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_scheduled_message_stops_retry_after_max(ad_hoc_bot_message, mock_retry_task):
    """Test that ScheduledMessage stops retrying after 3 attempts"""
    ad_hoc_bot_message.side_effect = Exception("Forced failure for max retries")

    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period="minutes", repetitions=1, experiment_id=session.experiment.id
    )
    sm = ScheduledMessageFactory(
        participant=session.participant,
        action=event_action,
        team=session.team,
        experiment=session.experiment,
    )
    sm.next_trigger_date = timezone.now() - relativedelta(minutes=1)
    sm.save()

    # First failure
    poll_scheduled_messages()
    sm.refresh_from_db()
    assert ScheduledMessageAttempt.objects.count() == 1

    # Second failure
    retry_scheduled_message(scheduled_message_id=sm.id, attempt_number=2)
    sm.refresh_from_db()
    assert ScheduledMessageAttempt.objects.count() == 2

    # Third failure
    retry_scheduled_message(scheduled_message_id=sm.id, attempt_number=3)
    sm.refresh_from_db()
    assert ScheduledMessageAttempt.objects.count() == 3

    assert mock_retry_task.call_count == 2

    attempts = ScheduledMessageAttempt.objects.filter(scheduled_message=sm).order_by("attempt_number")
    assert all(a.attempt_result == EventLogStatusChoices.FAILURE for a in attempts)
    assert attempts.last().attempt_number == 3


@pytest.mark.django_db()
@patch("apps.events.tasks.retry_scheduled_message.apply_async")
@patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
def test_scheduled_message_trace_info_success_and_failure(ad_hoc_bot_message, mock_retry_task):
    """Test that trace info is saved correctly for success and failure"""
    # On first call, success with trace_info
    ad_hoc_bot_message.return_value = {"trace_id": "xyz123", "trace_provider": "langfuse"}

    session = ExperimentSessionFactory()
    event_action, params = _construct_event_action(
        frequency=1, time_period="minutes", repetitions=1, experiment_id=session.experiment.id
    )
    sm = ScheduledMessageFactory(
        participant=session.participant,
        action=event_action,
        team=session.team,
        experiment=session.experiment,
    )
    sm.next_trigger_date = timezone.now() - relativedelta(minutes=1)
    sm.save()

    poll_scheduled_messages()
    sm.refresh_from_db()

    success_attempt = ScheduledMessageAttempt.objects.get(
        scheduled_message=sm, attempt_result=EventLogStatusChoices.SUCCESS
    )
    assert success_attempt.trace_info == {"trace_id": "xyz123", "trace_provider": "langfuse"}

    # Failed call
    def fail_with_trace(*args, **kwargs):
        e = Exception("Bot failed!")
        e.trace_metadata = {"trace_id": "fail456"}
        raise e

    ad_hoc_bot_message.side_effect = fail_with_trace
    retry_scheduled_message(scheduled_message_id=sm.id, attempt_number=2)

    failure_attempt = ScheduledMessageAttempt.objects.get(
        scheduled_message=sm, attempt_result=EventLogStatusChoices.FAILURE
    )
    assert failure_attempt.trace_info == {"trace_id": "fail456"}
    assert "Bot failed!" in failure_attempt.log_message
