from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.events.models import TimePeriod

REMINDER_MESSAGE_HELP_TEXT = (
    "What the reminder is about. This will be used to generate the reminder message for the user."
)


class WeekdaysEnum(int, Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class RecurringReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="The first (or only) reminder start date in ISO 8601 format")
    every: int = Field(description="Number of 'periods' to wait between reminders")
    period: TimePeriod = Field(description="The time period used in conjunction with 'every'")
    datetime_end: datetime | None = Field(description="The date of the last reminder in ISO 8601 format", default=None)
    message: str = Field(description=REMINDER_MESSAGE_HELP_TEXT)
    repetitions: str | None = Field(description="The number of repetitions", default=None)


class OneOffReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="The datetime that the reminder is due in ISO 8601 format")
    message: str = Field(description=REMINDER_MESSAGE_HELP_TEXT)


class DeleteReminderSchema(BaseModel):
    message_id: str = Field(description="The ID of the scheduled message to delete")


class ScheduledMessageSchema(BaseModel):
    message_id: str = Field(description="The UD of the scheduled message to update")
    weekday: WeekdaysEnum = Field(description="The new day of the week")
    hour: int = Field(description="The new hour of the day, in UTC")
    minute: int = Field(description="The new minute of the hour")
    specified_date: datetime | None = Field(description="True if the user requested a specific date", default=None)


class UpdateUserDataSchema(BaseModel):
    key: str = Field(description="The key in the user data to update")
    value: Any = Field(description="The new value of the user data")
