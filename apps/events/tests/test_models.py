import logging
from datetime import datetime
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from freezegun import freeze_time

from apps.events.models import ScheduledMessage, TimePeriod
from apps.events.tasks import _get_messages_to_fire, poll_scheduled_messages
from apps.events.tests.utils import construct_event_action
from apps.utils.factories.events import ScheduledMessageFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.time import timedelta_to_relative_delta


@pytest.mark.django_db()
class TestScheduledMessageModel:
    @pytest.mark.parametrize("period", ["hours", "days", "weeks"])
    @patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
    def test_create_scheduled_message_sets_start_date_and_external_id(self, ad_hoc_bot_message, period):
        session = ExperimentSessionFactory()
        event_action, params = construct_event_action(
            time_period=TimePeriod(period), experiment_id=session.experiment.id
        )
        with freeze_time("2024-01-01"):
            message = ScheduledMessage.objects.create(
                participant=session.participant, team=session.team, action=event_action, experiment=session.experiment
            )
            delta = relativedelta(**{params["time_period"]: params["frequency"]})
            rel_delta = timedelta_to_relative_delta(message.next_trigger_date - timezone.now())
            assert rel_delta == delta
            assert message.external_id is not None
            assert message.external_id != ""

    @pytest.mark.django_db()
    @patch("apps.channels.forms.TelegramChannelForm._set_telegram_webhook")
    def test_error_when_sending_sending_message_to_a_user(self, _set_telegram_webhook, caplog):
        """This test makes sure that any error that happens when sending a message to a user does not affect other
        pending messages"""

        session = ExperimentSessionFactory()
        event_action, params = construct_event_action(
            frequency=1, time_period=TimePeriod.DAYS, repetitions=2, experiment_id=session.experiment.id
        )
        with (
            caplog.at_level(logging.ERROR),
            patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message", side_effect=Exception("Oops")),
            patch("apps.events.tasks.functions.Now") as db_time,
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

    def _assert_next_trigger_date(self, message: ScheduledMessage, expected_date: datetime):
        message.refresh_from_db()
        assert message.next_trigger_date == expected_date

    @pytest.mark.django_db()
    def test_schedule_update(self):
        """Tests that a frequency update affects scheduled messages in the following way:
        if last_triggered_at is None, set next_trigger_date as created_at + delta
        if last_triggered_at is not None, set next_trigger_date as last_triggered_at + delta
        """
        session = ExperimentSessionFactory()
        experiment = session.experiment
        team = experiment.team
        session2 = ExperimentSessionFactory(team=team, experiment=experiment)
        event_action, params = construct_event_action(
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

        self._assert_next_trigger_date(message1, message1.created_at + new_delta)
        self._assert_next_trigger_date(message2, message2.last_triggered_at + new_delta)
        # Since message3 is completed, its `next_trigger_date` and `is_complete` should not change
        self._assert_next_trigger_date(message3, message3_next_trigger_data)
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

        self._assert_next_trigger_date(message1, message1.created_at + new_delta)
        self._assert_next_trigger_date(message2, message2.last_triggered_at + new_delta)
        self._assert_next_trigger_date(message3, message3_next_trigger_data)
        assert message1.next_trigger_date < message1_prev_trigger_date
        assert message2.next_trigger_date < message2_prev_trigger_date

    @patch("apps.experiments.models.ExperimentSession.ad_hoc_bot_message")
    def test_default_experiment_version_is_used_to_generate_message(self, ad_hoc_bot_message):
        session = ExperimentSessionFactory()
        working_experiment = session.experiment
        new_version = working_experiment.create_new_version()
        event_action, _params = construct_event_action(frequency=1, time_period=TimePeriod.WEEKS, repetitions=4)
        message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=event_action,
            last_triggered_at=timezone.now() - relativedelta(days=5),
            experiment=working_experiment,
        )

        message._trigger()
        experiment_used = ad_hoc_bot_message.call_args[1]["use_experiment"]
        assert experiment_used == new_version
