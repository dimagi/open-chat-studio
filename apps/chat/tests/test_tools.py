from datetime import datetime
from unittest import mock

import pytest
import pytz
from django.utils import timezone
from freezegun import freeze_time

from apps.chat.agent import tools
from apps.chat.agent.schemas import WeekdaysEnum
from apps.chat.agent.tools import (
    TOOL_CLASS_MAP,
    DeleteReminderTool,
    UpdateParticipantDataTool,
    _move_datetime_to_new_weekday_and_time,
    create_schedule_message,
)
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import AgentTools, Experiment
from apps.utils.factories.events import EventActionFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.time import pretty_date


class BaseTestAgentTool:
    tool_cls: type[tools.CustomBaseTool]

    def _invoke_tool(self, session, **tool_kwargs):
        tool = self.tool_cls(experiment_session=session)
        return tool.action(**tool_kwargs)

    @staticmethod
    def schedule_params():
        return {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "Testy"}

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestOneOffReminderTool(BaseTestAgentTool):
    tool_cls = tools.OneOffReminderTool

    def test_success(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "message": "Hi there",
            "schedule_name": "test",
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] is None
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] == 0
        assert message.custom_schedule_params["time_period"] == ""


@pytest.mark.django_db()
class TestRecurringReminderTool(BaseTestAgentTool):
    tool_cls = tools.RecurringReminderTool

    def test_with_repetitions(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": None,
            "repetitions": 2,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] == 2

    def test_with_end_time(self, session):
        datetime_due = timezone.now()
        datetime_end = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": datetime_end,
            "repetitions": None,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        assert message.end_date == datetime_end
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] is None

    def test_without_repetitions_or_end_time(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": None,
            "repetitions": None,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] is None


@pytest.mark.django_db()
class TestMoveScheduledMessageDateTool(BaseTestAgentTool):
    tool_cls = tools.MoveScheduledMessageDateTool

    def test_user_cannot_set_custom_date_for_system_created_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=self.schedule_params()),
            experiment=session.experiment,
        )

        response = self._invoke_tool(
            session,
            message_id=scheduled_message.external_id,
            weekday=WeekdaysEnum.MONDAY,
            hour=8,
            minute=0,
            specified_date=timezone.now(),
        )
        assert response == "The user cannot do that. Only weekdays and time of day can be changed"

    def test_user_can_set_custom_date_for_their_messages(self, session):
        with freeze_time("2024-01-01"):
            scheduled_message = ScheduledMessage.objects.create(
                participant=session.participant,
                team=session.team,
                experiment=session.experiment,
                custom_schedule_params=self.schedule_params(),
            )

            self._invoke_tool(
                session,
                message_id=scheduled_message.external_id,
                weekday=WeekdaysEnum.MONDAY,
                hour=8,
                minute=0,
                specified_date=timezone.now(),
            )
            scheduled_message.refresh_from_db()
            expected_date = pretty_date(scheduled_message.next_trigger_date)
            assert expected_date == "Monday, 01 January 2024 00:00:00 UTC"

    def test_update_schedule_tool(self, session):
        with freeze_time("2024-01-01"):
            message = ScheduledMessage.objects.create(
                participant=session.participant,
                team=session.team,
                action=EventActionFactory(params=self.schedule_params()),
                experiment=session.experiment,
            )

            expected_date = pretty_date(message.next_trigger_date)
            assert expected_date == "Tuesday, 02 January 2024 00:00:00 UTC"

            response = self._invoke_tool(
                session,
                message_id=message.external_id,
                weekday=WeekdaysEnum.FRIDAY,
                hour=8,
                minute=0,
                specified_date=None,
            )
            message.refresh_from_db()
            expected_date = pretty_date(message.next_trigger_date)
            assert expected_date == "Friday, 05 January 2024 08:00:00 UTC"
            assert response == f"The new datetime is {expected_date}"


@pytest.mark.django_db()
class TestDeleteReminderTool:
    def _invoke_tool(self, session, **tool_kwargs):
        tool = DeleteReminderTool(experiment_session=session)
        return tool.action(**tool_kwargs)

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()

    @staticmethod
    def schedule_params():
        return {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "Testy"}

    def test_user_cannot_delete_system_scheduled_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=self.schedule_params()),
            experiment=session.experiment,
        )

        response = self._invoke_tool(session, message_id=scheduled_message.external_id)
        assert response == "Cannot delete this reminder"
        scheduled_message.refresh_from_db()
        assert scheduled_message.cancelled_at is None

    def test_user_can_delete_their_scheduled_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            experiment=session.experiment,
            custom_schedule_params=self.schedule_params(),
        )
        response = self._invoke_tool(session, message_id=scheduled_message.external_id)
        assert response == "Success"
        scheduled_message.refresh_from_db()
        assert scheduled_message.cancelled_at is not None

    def test_specified_message_does_not_exist(self, session):
        response = self._invoke_tool(session, message_id="gone with the wind")
        assert response == "Could not find this reminder"


@pytest.mark.parametrize(
    ("initial_datetime_str", "new_weekday", "new_hour", "new_minute", "expected_new_datetime_str"),
    [
        ("2024-05-08 08:00:00", 2, 12, 0, "2024-05-08 12:00:00"),  # Only time change
        ("2024-05-08 08:00:00", 0, 8, 0, "2024-05-06 08:00:00"),  # Wednesday to Monday
        ("2024-05-29 08:00:00", 4, 8, 0, "2024-05-31 08:00:00"),  # Wednesday to Friday
        ("2024-06-01 08:00:00", 0, 8, 0, "2024-05-27 08:00:00"),  # Saturday to Monday
        ("2024-06-02 08:00:00", 0, 8, 0, "2024-05-27 08:00:00"),  # Sunday to Monday
        ("2024-06-02 08:00:00", 1, 8, 0, "2024-05-28 08:00:00"),  # Sunday to Tuesday
    ],
)
def test_move_datetime_to_new_weekday_and_time(
    initial_datetime_str, new_weekday, new_hour, new_minute, expected_new_datetime_str
):
    """Test weekday and time changes. A weekday change will not cause the datetime to jump to a different week"""
    initial_datetime = datetime.strptime(initial_datetime_str, "%Y-%m-%d %H:%M:%S")
    initial_datetime = initial_datetime.astimezone(pytz.UTC)
    new_datetime = _move_datetime_to_new_weekday_and_time(
        initial_datetime, new_weekday=new_weekday, new_hour=new_hour, new_minute=new_minute
    )
    assert new_datetime.strftime("%Y-%m-%d %H:%M:%S") == expected_new_datetime_str


@pytest.mark.django_db()
def test_create_schedule_message_success():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": 1,
        "time_period": "days",
        "repetitions": 2,
    }
    start_date = timezone.now()
    end_date = timezone.now()
    response = create_schedule_message(
        experiment_session, message, name="Test", start_date=start_date, end_date=end_date, is_recurring=True, **kwargs
    )
    assert response == "Success: scheduled message created"

    scheduled_message = ScheduledMessage.objects.filter(
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        team=experiment_session.team,
    ).first()

    assert scheduled_message is not None
    assert scheduled_message.custom_schedule_params["name"] == "Test"
    assert scheduled_message.custom_schedule_params["prompt_text"] == message
    assert scheduled_message.custom_schedule_params["frequency"] == kwargs["frequency"]
    assert scheduled_message.custom_schedule_params["time_period"] == kwargs["time_period"]
    assert scheduled_message.custom_schedule_params["repetitions"] == kwargs["repetitions"]
    assert scheduled_message.action is None
    assert scheduled_message.next_trigger_date == start_date
    assert scheduled_message.end_date == end_date


@pytest.mark.django_db()
def test_create_schedule_message_invalid_form():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": "invalid_frequency",  # invalid input
        "time_period": "days",
        "repetitions": 2,
    }

    response = create_schedule_message(
        experiment_session, message, name="Test", start_date=None, is_recurring=True, **kwargs
    )
    assert response == "Could not create scheduled message"

    scheduled_message_count = ScheduledMessage.objects.filter(
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        team=experiment_session.team,
    ).count()

    assert scheduled_message_count == 0


@pytest.mark.django_db()
def test_create_schedule_message_experiment_does_not_exist():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": 1,
        "time_period": "days",
        "repetitions": 2,
    }

    with mock.patch("django.db.transaction.atomic", side_effect=Experiment.DoesNotExist):
        response = create_schedule_message(
            experiment_session, message, name="Test", start_date=None, is_recurring=True, **kwargs
        )
        assert response == "Experiment does not exist! Could not create scheduled message"

        scheduled_message_count = ScheduledMessage.objects.filter(
            experiment=experiment_session.experiment,
            participant=experiment_session.participant,
            team=experiment_session.team,
        ).count()

        assert scheduled_message_count == 0


@pytest.mark.django_db()
class TestUpdateParticipantDataTool:
    def _invoke_tool(self, session, **tool_kwargs):
        tool = UpdateParticipantDataTool(experiment_session=session)
        return tool.action(**tool_kwargs)

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()

    @pytest.mark.parametrize(
        "value",
        [
            "string",
            1,
            1.0,
            True,
            False,
            None,
            ["hi", "there"],
            {"key": "value"},
            [{"key": "value"}],
        ],
    )
    def test_update(self, session, value):
        response = self._invoke_tool(session, key="test", value=value)
        assert response == "Success"

        assert session.participant_data_from_experiment == {"test": value}


def test_tools_present():
    non_user_facing_tools = [AgentTools.ATTACH_MEDIA]
    for tool in AgentTools.values:
        if tool in non_user_facing_tools:
            continue
        assert tool in TOOL_CLASS_MAP
