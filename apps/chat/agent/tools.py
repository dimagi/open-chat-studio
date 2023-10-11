import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional, Type

import pytz
from django_celery_beat.models import ClockedSchedule, IntervalSchedule, PeriodicTask
from langchain.tools.base import BaseTool

from apps.channels.models import ChannelSession
from apps.chat.agent import schemas
from apps.experiments.models import ExperimentSession

BOT_MESSAGE_FOR_USER_TASK = "apps.chat.tasks.send_bot_message_to_users"


class CustomBaseTool(BaseTool):
    experiment_session: Optional[ExperimentSession] = None
    # Some tools like the reminder requires a chat session id in order to get back to the user later
    requires_session = False

    def _run(self, *args, **kwargs):
        if self.requires_session and not self.experiment_session:
            return "I am unable to do this"
        try:
            return self.action(*args, **kwargs)
        except Exception as e:
            logging.error(e)
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
    args_schema: Type[schemas.RecurringReminderSchema] = schemas.RecurringReminderSchema

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
        periodic_task_kwargs = {
            "start_time": datetime_due,
            "expires": datetime_end,
            "interval": interval_schedule,
        }
        create_periodic_task(self.experiment_session, message=message, kwargs=periodic_task_kwargs)
        return "Success"


class OneOffReminderTool(CustomBaseTool):
    name = "one-off-reminder"
    description = "useful to schedule one-off reminders"
    requires_session = True
    args_schema: Type[schemas.OneOffReminderSchema] = schemas.OneOffReminderSchema

    def action(
        self,
        datetime_due: datetime,
        message: str,
        **kwargs,
    ):
        periodic_task_kwargs = {
            "clocked": ClockedSchedule.objects.create(clocked_time=datetime_due),
            "one_off": True,
        }
        create_periodic_task(self.experiment_session, message=message, kwargs=periodic_task_kwargs)
        return "Success"


def create_periodic_task(experiment_session: ExperimentSession, message: str, kwargs: dict):
    channel_session = ChannelSession.objects.filter(experiment_session=experiment_session).first()
    task_kwargs = json.dumps(
        {"chat_ids": [channel_session.external_chat_id], "message": message, "is_bot_instruction": False}
    )
    PeriodicTask.objects.create(
        name=f"reminder-{experiment_session.id}-{uuid.uuid4()}",
        task=BOT_MESSAGE_FOR_USER_TASK,
        kwargs=task_kwargs,
        **kwargs,
    )


tools: List[CustomBaseTool] = (RecurringReminderTool(), OneOffReminderTool())
