from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from apps.events.models import TimePeriod


class WeekdaysEnum(int, Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATERDAY = 6
    SUNDAY = 7


class RecurringReminderSchema(BaseModel):
    datetime_due: datetime | None = Field(description="the first (or only) reminder start date in ISO 8601 format")
    every: int = Field(description="Number of periods to wait between reminders")
    period: TimePeriod = Field(description="The time period between reminders")
    datetime_end: datetime | None = Field(description="the date of the last reminder in ISO 8601 format", default=None)
    message: str = Field(description="The reminder message")
    repetitions: str | None = Field(description="The number of repetitions", default=None)


class OneOffReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="the datetime that the reminder is due in ISO 8601 format")
    message: str = Field(description="The reminder message")


class DeleteReminderSchema(BaseModel):
    message_id: str = Field(description="the id of the scheduled message")


class ScheduledMessageSchema(BaseModel):
    message_id: str = Field(description="the id of the scheduled message")
    weekday: WeekdaysEnum = Field(description="The day of the week")
    hour: int = Field(description="The hour of the day")
    minute: int = Field(description="The minute of the hour")
    user_specified_custom_date: bool = Field(description="True if the user specifies a date")
