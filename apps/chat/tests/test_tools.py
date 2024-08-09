from datetime import datetime
from unittest import mock

import pytest
import pytz
from django.utils import timezone
from freezegun import freeze_time

from apps.chat.agent.schemas import WeekdaysEnum
from apps.chat.agent.tools import (
    DeleteReminderTool,
    UpdateScheduledMessageTool,
    _move_datetime_to_new_weekday_and_time,
    create_schedule_message,
)
from apps.events.models import ScheduledMessage
from apps.experiments.models import Experiment
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
    response = create_schedule_message(experiment_session, message, start_date=start_date, end_date=end_date, **kwargs)
    assert response == "Success: scheduled message created"

    scheduled_message = ScheduledMessage.objects.filter(
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        team=experiment_session.team,
    ).first()

    assert scheduled_message is not None
    assert scheduled_message.custom_schedule_params["name"].startswith("schedule_message_")
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

    response = create_schedule_message(experiment_session, message, start_date=None, **kwargs)
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
        response = create_schedule_message(experiment_session, message, start_date=None, **kwargs)
        assert response == "Experiment does not exist! Could not create scheduled message"

        scheduled_message_count = ScheduledMessage.objects.filter(
            experiment=experiment_session.experiment,
            participant=experiment_session.participant,
            team=experiment_session.team,
        ).count()

        assert scheduled_message_count == 0


@pytest.mark.django_db()
def test_delete_schedule_tool():
    session = ExperimentSessionFactory()
    with freeze_time("2024-01-01"):
        params = {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "Testy"}
        system_scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=params),
            experiment=session.experiment,
        )
        user_scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            experiment=session.experiment,
            custom_schedule_params=params,
        )

        tool = DeleteReminderTool(experiment_session=session)

        # User should not be able to delete this one
        response = tool.action(message_id=system_scheduled_message.external_id)
        assert response == "Cannot delete this reminder"
        system_scheduled_message.refresh_from_db()

        # User should be able to delete this one
        response = tool.action(message_id=user_scheduled_message.external_id)
        assert response == "Success"
        with pytest.raises(ScheduledMessage.DoesNotExist):
            user_scheduled_message.refresh_from_db()

        # Bot cannot find the scheduled message
        response = tool.action(message_id="gone with the wind")
        assert response == "Could not find this reminder"
