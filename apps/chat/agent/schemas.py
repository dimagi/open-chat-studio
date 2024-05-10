from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


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
    weekday: str = Field(description="The day of the week")
