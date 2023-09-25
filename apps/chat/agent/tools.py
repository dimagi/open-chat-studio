import logging
from datetime import datetime, timedelta
from typing import List, Optional, Type

import pytz
from langchain.tools.base import BaseTool

from apps.chat.agent.schemas import ReminderSchema
from apps.chat.models import FutureMessage
from apps.experiments.models import ExperimentSession


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


class CurrentDatetimeTool(CustomBaseTool):
    name = "current_datetime"
    description = "useful for getting the current datetime and timezone in ISO 8601. this should be called before setting any reminders"

    def action(self, *args, **kwargs):
        timezone = pytz.timezone("Africa/Johannesburg")
        current_datetime = datetime.now().astimezone(timezone)
        iso_format = current_datetime.strftime("%Y-%m-%dT%H:%M:%S%z")
        return str(iso_format)


class ReminderTool(CustomBaseTool):
    name = "reminder"
    description = "useful tool for reminding users and getting back to them later"
    requires_session = True
    args_schema: Type[ReminderSchema] = ReminderSchema

    def action(
        self,
        datetime_due: datetime,
        repeating: bool,
        interval_minutes: Optional[int],
        reminder_message: str,
        periods: Optional[int],
        **kwargs,
    ):
        end_date = datetime_due
        if repeating and interval_minutes and periods:
            end_date = datetime_due + timedelta(minutes=interval_minutes) * periods
        future_message = FutureMessage.objects.create(
            message=reminder_message,
            due_at=datetime_due,
            experiment_session=self.experiment_session,
            interval_minutes=interval_minutes if repeating else 0,
            end_date=end_date,
        )
        future_message.save()
        return "Success"


tools: List[CustomBaseTool] = (CurrentDatetimeTool(), ReminderTool())
