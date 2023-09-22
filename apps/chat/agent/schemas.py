from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="reminder due date in ISO 8601 format")
    repeating: bool = Field(description="indicates whether this reminder should be repeating")
    periods: int = Field(description="For repeating reminders, this is the number of periods to repeat for")
    interval_minutes: int = Field(
        description="For repeating reminders, this is the interval between periods in minutes"
    )
    reminder_message: str = Field(description="The message of the reminder")
