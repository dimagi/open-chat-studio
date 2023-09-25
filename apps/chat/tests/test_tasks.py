from datetime import datetime, timedelta

import pytz
from django.test import TestCase
from freezegun import freeze_time
from mock import Mock, patch

from apps.channels.models import ChannelSession, ExperimentChannel
from apps.chat.exceptions import ExperimentChannelRepurposedException
from apps.chat.models import ChatMessage, FutureMessage
from apps.chat.tasks import (
    _check_future_messages,
    _get_appropriate_ping_response,
    _no_activity_pings,
    _try_send_message,
    _update_future_message_due_at,
)
from apps.experiments.models import Experiment, ExperimentSession, NoActivityMessageConfig, Prompt, SessionStatus
from apps.experiments.views import _start_experiment_session
from apps.users.models import CustomUser


class TasksTest(TestCase):
    def setUp(self):
        super().setUp()
        self.telegram_chat_id = 1234567891
        self.user = CustomUser.objects.create_user(username="testuser")
        self.prompt = Prompt.objects.create(
            owner=self.user,
            name="test-prompt",
            description="test",
            prompt="You are a helpful assistant",
        )
        self.no_activity_config = NoActivityMessageConfig.objects.create(
            message_for_bot="Some message", name="Some name", max_pings=3, ping_after=1
        )
        self.experiment = Experiment.objects.create(
            owner=self.user,
            name="TestExperiment",
            description="test",
            chatbot_prompt=self.prompt,
            no_activity_config=self.no_activity_config,
        )
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel", experiment=self.experiment, extra_data={"bot_token": "123123123"}, platform="telegram"
        )
        self.experiment_session = self._add_session(self.experiment)

    @patch("apps.chat.bots.TopicBot._get_response")
    @patch("apps.chat.bots.create_conversation")
    def test_getting_ping_message_saves_history(self, create_conversation, _get_response_mock):
        create_conversation.return_value = Mock()
        expected_ping_message = "Hey, answer me!"
        _get_response_mock.return_value = expected_ping_message
        response = _get_appropriate_ping_response(self.experiment_session, "Some message")
        messages = ChatMessage.objects.filter(chat=self.experiment_session.chat).all()
        # Only the AI message should be there
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_type, "ai")
        self.assertEqual(response, expected_ping_message)
        self.assertEqual(messages[0].content, expected_ping_message)

    @patch("apps.chat.tasks._get_appropriate_ping_response", return_value="Please answer")
    @patch("apps.chat.tasks._try_send_message")
    def test_no_activity_ping_triggered_for_active_sessions(self, _get_appropriate_ping_response, _try_send_message):
        second_experiment = Experiment.objects.create(
            owner=self.user,
            name="TestExperiment2",
            description="test2",
            chatbot_prompt=self.prompt,
            no_activity_config=None,
        )
        ExperimentChannel.objects.create(
            name="TestChannel2", experiment=second_experiment, extra_data={"bot_token": "222222"}, platform="telegram"
        )
        # Experiment sessions which should be pinged
        experiment_session_should_fire = self.experiment_session
        self._add_chats(experiment_session_should_fire, last_message_type="ai")
        experiment_session_setup = self._add_session(self.experiment, session_status=SessionStatus.SETUP)
        self._add_chats(experiment_session_setup, last_message_type="ai")

        # Experiment sessions for which no ping should trigger
        # See the docstring for `_no_activity_pings` for the criteria of a ping message to be triggered
        # Criteria number 1 not met
        self._add_session(self.experiment, session_status=SessionStatus.ACTIVE)
        # Criteria number 2 not met
        experiment_session_setup = self._add_session(self.experiment, session_status=SessionStatus.PENDING_REVIEW)
        self._add_chats(experiment_session_setup, last_message_type="ai")
        experiment_session_completed = self._add_session(self.experiment, session_status=SessionStatus.COMPLETE)
        self._add_chats(experiment_session_completed, last_message_type="ai")
        experiment_session_completed = self._add_session(self.experiment, session_status=SessionStatus.UNKNOWN)
        self._add_chats(experiment_session_completed, last_message_type="ai")
        # Criteria number 3 not met
        experiment_session_no_config = self._add_session(second_experiment, session_status=SessionStatus.ACTIVE)
        self._add_chats(experiment_session_no_config, last_message_type="ai")
        # Criteria number 4 not met
        experiment_session_not_eligible = self._add_session(self.experiment, session_status=SessionStatus.SETUP)
        self._add_chats(experiment_session_not_eligible, last_message_type="human")

        # frozen_time = "2023-08-21 12:00:00"  # Set the desired frozen time
        with freeze_time(datetime.utcnow() + timedelta(minutes=5)):
            _no_activity_pings()
        self.assertEqual(_try_send_message.call_count, 2)

    def _add_session(self, experiment: Experiment, session_status: SessionStatus = SessionStatus.ACTIVE):
        experiment_session = _start_experiment_session(experiment)
        experiment_session.status = session_status
        experiment_session.save()
        ChannelSession.objects.create(
            external_chat_id=self.telegram_chat_id,
            experiment_session=experiment_session,
            experiment_channel=self.experiment_channel,
        )
        return experiment_session

    def _add_chats(self, experiment_session: ExperimentSession, last_message_type: str):
        ChatMessage.objects.create(chat=experiment_session.chat, message_type="human", content="Hi")
        if last_message_type == "ai":
            ChatMessage.objects.create(
                chat=experiment_session.chat, message_type="ai", content="Hello. How can I assist you today?"
            )

    @patch("apps.chat.message_handlers.TelegramMessageHandler.new_bot_message")
    def test_sending_future_message_updates_chat_history(self, new_bot_message_mock):
        timezone = pytz.timezone("Africa/Johannesburg")
        due_at = datetime.now().astimezone(timezone)
        FutureMessage.objects.create(
            message="Remember tests!",
            due_at=due_at,
            interval_minutes=0,
            end_date=due_at,
            experiment_session=self.experiment_session,
        )

        # Simulate the task run
        before_count = ChatMessage.objects.filter(chat=self.experiment_session.chat).count()
        _check_future_messages()
        after_count = ChatMessage.objects.filter(chat=self.experiment_session.chat).count()
        new_bot_message_mock.assert_called()
        future_message = FutureMessage.objects.first()
        self.assertTrue(future_message.resolved)
        self.assertEqual(after_count, before_count + 1)

    @patch("apps.chat.tasks._try_send_message")
    def test_recurrent_future_message_scheduler(self, send_message_mock):
        """
        This test case simulates the behavior of scheduling and sending future messages within a given interval.
        It covers the following scenarios:

        1. A future message is created to be sent at specific intervals over a period.
        2. The first message is missed, but is picked up and scheduled in the current period.
        3. The test ensures that messages are correctly scheduled for the next period and that they are not sent
            before their due date.
        4. The final state of the message is verified

        The test involves freezing time to simulate different points in the future, allowing for the testing of
        various scenarios, including:
        - Missed due date handling.
        - Messages scheduled for future periods.
        - Messages sent only when due.
        - Resolution of the message after all periods have passed.
        """
        interval_minutes = 24 * 60
        periods = 2  # So 3 messages expected in total
        current_datetime = datetime.now().astimezone(pytz.timezone("UTC"))
        message = "Remember tests!"

        # First message should have been sent the previous period, but it was missed
        original_due_at = current_datetime - timedelta(days=1)
        end_time = original_due_at + timedelta(minutes=interval_minutes) * periods

        future_message = FutureMessage.objects.create(
            message=message,
            due_at=original_due_at,
            end_date=end_time,
            experiment_session=self.experiment_session,
            interval_minutes=interval_minutes,
        )
        # _update_future_message_due_at(future_message)
        future_message = FutureMessage.objects.first()
        # First message was yesterday, so with 2x24hr periods the last one should be tomorrow
        expected_end_date = future_message.due_at + timedelta(minutes=interval_minutes) * periods
        self.assertEqual(future_message.due_at, original_due_at)
        self.assertEqual(future_message.end_date, expected_end_date)

        # Simulate task runs
        # Run 1 - Yesterday after the start time
        yesterday_after_missed_due_date = original_due_at + timedelta(minutes=25)
        with freeze_time(yesterday_after_missed_due_date):
            _check_future_messages()
            # Validations
            future_message.refresh_from_db()
            # Since the current run picked up the message, it schedules it for the next period
            next_expected_due_at = original_due_at + timedelta(minutes=interval_minutes)
            self.assertEqual(future_message.due_at, next_expected_due_at)
            self.assertFalse(future_message.resolved)
            self.assertEqual(send_message_mock.call_count, 1)

        # Run 2 - Today - before due date
        today_before_due_date = original_due_at + timedelta(minutes=interval_minutes - 60)
        with freeze_time(today_before_due_date):
            _check_future_messages()
            # Validations
            future_message.refresh_from_db()
            # due_at shoudld not change
            next_expected_due_at = original_due_at + timedelta(minutes=interval_minutes)
            self.assertEqual(future_message.due_at, next_expected_due_at)
            self.assertFalse(future_message.resolved)
            # The message should not have been sent, since it wasn't time yet
            self.assertEqual(send_message_mock.call_count, 1)

        # Run 3 - Today - after due date
        today_after_due_date = original_due_at + timedelta(minutes=interval_minutes + 60)
        with freeze_time(today_after_due_date):
            _check_future_messages()
            # Validations
            future_message.refresh_from_db()
            # Since the current run picked up the message, it schedules it for the next period
            next_expected_due_at = original_due_at + timedelta(minutes=interval_minutes) * 2
            self.assertEqual(future_message.due_at, next_expected_due_at)
            self.assertFalse(future_message.resolved)
            self.assertEqual(send_message_mock.call_count, 2)

        # Run 2 - Tomorrow - after due date
        tomorrow_after_due_time = original_due_at + timedelta(minutes=interval_minutes * 2)
        with freeze_time(tomorrow_after_due_time):
            _check_future_messages()
            # Validations
            future_message.refresh_from_db()
            self.assertEqual(send_message_mock.call_count, 3)
            self.assertTrue(future_message.resolved)

        # Run 3 - Tomorrow, after the final run that already sent out the last message
        with patch("apps.chat.tasks._try_send_message") as send_message_mock:
            with patch("apps.chat.tasks._update_future_message_due_at") as schedule_next_message_mock:
                with freeze_time(current_datetime + timedelta(days=1, hours=2, minutes=50)):
                    _check_future_messages()
                    # Validations
                    send_message_mock.assert_not_called()
                    schedule_next_message_mock.assert_not_called()

    @patch("apps.chat.tasks._try_send_message")
    def test_recurrent_message_is_sent_once_when_missed_completely(self, send_message_mock):
        interval_minutes = 24 * 60
        periods = 2
        current_datetime = datetime.now().astimezone(pytz.timezone("UTC"))
        expected_end_date = current_datetime + timedelta(minutes=interval_minutes) * periods
        message = "Remember tests!"
        far_in_the_future = current_datetime + timedelta(days=10)

        # First message should have been sent the previous period, but it was missed
        future_message = FutureMessage.objects.create(
            message=message,
            due_at=current_datetime,
            interval_minutes=interval_minutes,
            end_date=expected_end_date,
            experiment_session=self.experiment_session,
        )
        self.assertTrue(future_message.end_date < far_in_the_future)
        # Simulate task run

        with freeze_time(far_in_the_future):
            _check_future_messages()
            # Validations
            future_message.refresh_from_db()
            # Since the current run picked up the message, it schedules it for the next period
            self.assertTrue(future_message.resolved)
            self.assertEqual(send_message_mock.call_count, 1)

    def test_future_messages_resolved_when_experiment_channel_is_repurposed(self):
        interval_minutes = 24
        periods = 2
        current_datetime = datetime.now().astimezone(pytz.timezone("UTC"))
        expected_end_date = current_datetime + timedelta(minutes=interval_minutes) * periods
        message = "Remember tests!"

        future_message = FutureMessage.objects.create(
            message=message,
            due_at=current_datetime,
            interval_minutes=interval_minutes,
            end_date=expected_end_date,
            experiment_session=self.experiment_session,
        )

        new_experiment = Experiment.objects.create(
            owner=self.user, name="TestExperiment2", description="test2", chatbot_prompt=self.prompt
        )
        self.experiment_channel.experiment = new_experiment
        self.experiment_channel.save()
        self.assertNotEqual(self.experiment_channel.experiment, self.experiment_session.experiment)
        # with self.assertRaises(ExperimentChannelRepurposedException):

        _check_future_messages()
        future_message.refresh_from_db()
        self.assertTrue(future_message.resolved)
