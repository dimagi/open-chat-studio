import json
import logging
import uuid
from datetime import datetime, timedelta

from django_celery_beat.models import ClockedSchedule, IntervalSchedule, PeriodicTask
from langchain.tools.base import BaseTool

from apps.chat.agent import schemas
from apps.events.models import ScheduledMessage
from apps.experiments.models import ExperimentSession
from apps.utils.time import pretty_date

BOT_MESSAGE_FOR_USER_TASK = "apps.chat.tasks.send_bot_message_to_users"


class CustomBaseTool(BaseTool):
    experiment_session: ExperimentSession | None = None
    # Some tools like the reminder requires a chat session id in order to get back to the user later
    requires_session = False

    def _run(self, *args, **kwargs):
        if self.requires_session and not self.experiment_session:
            return "I am unable to do this"
        try:
            return self.action(*args, **kwargs)
        except Exception as e:
            logging.exception(e)
            return "Something went wrong"

    async def _arun(self, *args, **kwargs) -> str:
        """Use the tool asynchronously."""
        raise NotImplementedError("custom_search does not support async")

    def action(*args, **kwargs):
        raise Exception("Not implemented")


class RecurringReminderTool(CustomBaseTool):
    name = "recurring-reminder"
    description = "useful to schedule recurring reminders"
    requires_session = True
    args_schema: type[schemas.RecurringReminderSchema] = schemas.RecurringReminderSchema

    def action(
        self,
        datetime_due: datetime,
        datetime_end: datetime,
        every: int,
        period: str,
        message: str,
        **kwargs,
    ):
        interval_schedule, _created = IntervalSchedule.objects.get_or_create(every=every, period=period)
        create_periodic_task(
            self.experiment_session,
            message=message,
            start_time=datetime_due,
            expires=datetime_end,
            interval=interval_schedule,
        )
        return "Success"


class OneOffReminderTool(CustomBaseTool):
    name = "one-off-reminder"
    description = "useful to schedule one-off reminders"
    requires_session = True
    args_schema: type[schemas.OneOffReminderSchema] = schemas.OneOffReminderSchema

    def action(
        self,
        datetime_due: datetime,
        message: str,
        **kwargs,
    ):
        create_periodic_task(
            self.experiment_session,
            message=message,
            clocked=ClockedSchedule.objects.create(clocked_time=datetime_due),
            one_off=True,
        )
        return "Success"


class UpdateScheduledMessageTool(CustomBaseTool):
    name = "schedule-update-tool"
    description = "useful to update the schedule of a scheduled message. Use only to update existing schedules"
    requires_session = True
    args_schema: type[schemas.ScheduledMessageSchema] = schemas.ScheduledMessageSchema

    def action(
        self,
        name: str,
        weekday: schemas.WeekdaysEnum,
        hour: int,
        minute: int,
        user_specified_custom_date: bool,
    ):
        if user_specified_custom_date:
            return "The user cannot do that. Only weekdays and time of day can be changed"
        message = ScheduledMessage.objects.get(
            participant=self.experiment_session.participant, action__params__name=name
        )
        # the datetime object regard Monday as day 0 whereas the llm regards it as day 1
        weekday_int = weekday.value - 1
        message.next_trigger_date = _move_datetime_to_new_weekday_and_time(
            message.next_trigger_date, weekday_int, hour, minute
        )
        message.custom_schedule_params = {"weekday": weekday_int, "hour": hour, "minute": minute}
        message.save()

        return f"The new datetime is {pretty_date(message.next_trigger_date)}"


def _move_datetime_to_new_weekday_and_time(date: datetime, new_weekday: int, new_hour: int, new_minute: int):
    current_weekday = date.weekday()
    day_diff = new_weekday - current_weekday
    return date.replace(hour=new_hour, minute=new_minute, second=0) + timedelta(days=day_diff)


def create_periodic_task(experiment_session: ExperimentSession, message: str, **kwargs):
    task_kwargs = json.dumps(
        {
            "chat_ids": [experiment_session.participant.identifier],
            "message": message,
            "is_bot_instruction": False,
            "experiment_public_id": str(experiment_session.experiment.public_id),
        }
    )
    PeriodicTask.objects.create(
        name=f"reminder-{experiment_session.id}-{uuid.uuid4()}",
        task=BOT_MESSAGE_FOR_USER_TASK,
        kwargs=task_kwargs,
        **kwargs,
    )


def get_tools(experiment_session) -> list[BaseTool]:
    return [
        # RecurringReminderTool(experiment_session=experiment_session),
        # OneOffReminderTool(experiment_session=experiment_session),
        UpdateScheduledMessageTool(experiment_session=experiment_session),
    ]
