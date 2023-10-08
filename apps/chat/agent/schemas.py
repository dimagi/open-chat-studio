from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PeriodEnum(str, Enum):
    SECOND = "seconds"
    MINUTE = "minutes"
    HOUR = "hours"
    DAY = "days"


class RecurringReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="the first (or only) reminder due date in ISO 8601 format")
    every: int = Field(description="Number of interval periods to wait before the next reminder")
    period: PeriodEnum = Field(description="The type of period between reminders")
    datetime_end: datetime = Field(description="the date of the last reminder in ISO 8601 format")
    message: str = Field(description="The reminder message")


class OneOffReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="the datetime that the reminder is due in ISO 8601 format")
    message: str = Field(description="The reminder message")
