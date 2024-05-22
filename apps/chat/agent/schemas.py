from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class WeekdaysEnum(int, Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATERDAY = 6
    SUNDAY = 7


class PeriodEnum(str, Enum):
    SECOND = "seconds"
    MINUTE = "minutes"
    HOUR = "hours"
    DAY = "days"


class RecurringReminderSchema(BaseModel):
    name: str = Field(description="the name of the reminder")
    datetime_due: datetime = Field(description="the first (or only) reminder due date in ISO 8601 format")
    every: int = Field(description="Number of interval periods to wait before the next reminder")
    period: PeriodEnum = Field(description="The type of period between reminders")
    datetime_end: datetime = Field(description="the date of the last reminder in ISO 8601 format")
    message: str = Field(description="The reminder message")


class OneOffReminderSchema(BaseModel):
    name: str = Field(description="the name of the reminder")
    datetime_due: datetime = Field(description="the datetime that the reminder is due in ISO 8601 format")
    message: str = Field(description="The reminder message")


class ScheduledMessageSchema(BaseModel):
    name: str = Field(description="the name of the scheduled message")
    weekday: WeekdaysEnum = Field(description="The day of the week")
    hour: int = Field(description="The hour of the day")
    minute: int = Field(description="The minute of the hour")
    user_specified_custom_date: bool = Field(description="True if the user specifies a date")
