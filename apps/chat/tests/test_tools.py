from datetime import datetime

import pytest
import pytz
from freezegun import freeze_time

from apps.chat.agent.schemas import WeekdaysEnum
from apps.chat.agent.tools import UpdateScheduledMessageTool, _move_datetime_to_new_weekday_and_time
from apps.events.models import ScheduledMessage
from apps.utils.factories.events import EventActionFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.time import pretty_date


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
def test_update_schedule_tool():
    session = ExperimentSessionFactory()
    with freeze_time("2024-01-01"):
        params = {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "A test schedule"}
        message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=params),
            experiment=session.experiment,
        )

        expected_date = pretty_date(message.next_trigger_date)
        assert expected_date == "Tuesday, 02 January 2024 00:00:00 UTC"

        tool = UpdateScheduledMessageTool(experiment_session=session)
        response = tool.action(
            name="A test schedule", weekday=WeekdaysEnum.FRIDAY, hour=8, minute=0, user_specified_custom_date=False
        )
        message.refresh_from_db()
        expected_date = pretty_date(message.next_trigger_date)
        assert expected_date == "Friday, 05 January 2024 08:00:00 UTC"
        assert response == f"The new datetime is {expected_date}"


@pytest.mark.django_db()
def test_user_cannot_set_custom_date():
    tool = UpdateScheduledMessageTool(experiment_session=ExperimentSessionFactory())
    response = tool.action(
        name="A test schedule", weekday=WeekdaysEnum.MONDAY, hour=8, minute=0, user_specified_custom_date=True
    )
    assert response == "The user cannot do that. Only weekdays and time of day can be changed"
