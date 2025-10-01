from datetime import datetime
from enum import Enum

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
    datetime_end: datetime = Field(description="The date of the last reminder in ISO 8601 format", default=None)
    message: str = Field(description=REMINDER_MESSAGE_HELP_TEXT)
    repetitions: str = Field(description="The number of repetitions", default=None)
    schedule_name: str = Field(description="The name for this scheduled message")


class OneOffReminderSchema(BaseModel):
    datetime_due: datetime = Field(description="The datetime that the reminder is due in ISO 8601 format")
    message: str = Field(description=REMINDER_MESSAGE_HELP_TEXT)
    schedule_name: str = Field(description="The name for this scheduled message")


class DeleteReminderSchema(BaseModel):
    message_id: str = Field(description="The ID of the scheduled message to delete")


class ScheduledMessageSchema(BaseModel):
    message_id: str = Field(description="The ID of the scheduled message to update")
    weekday: WeekdaysEnum = Field(description="The new day of the week")
    hour: int = Field(description="The new hour of the day, in UTC")
    minute: int = Field(description="The new minute of the hour")
    specified_date: datetime = Field(
        description="A specific date to re-schedule the message for in ISO 8601 format", default=None
    )


class UpdateUserDataSchema(BaseModel):
    key: str = Field(description="The key in the user data to update")
    value: str | int | dict | list = Field(description="The new value of the user data")


class AppendToParticipantData(BaseModel):
    key: str = Field(description="The key in the user data to append to")
    value: str | int | list = Field(description="The value to append")


class IncrementCounterSchema(BaseModel):
    counter: str = Field(description="The name of the counter to increment")
    value: int = Field(description="The value to increment the counter by", default=1)


class AttachMediaSchema(BaseModel):
    file_ids: list[int] = Field(description="The IDs of the media files to attach (max 5)")


class SearchIndexSchema(BaseModel):
    query: str = Field(
        description="A natural language query to search for relevant information in the documents. "
        "Be specific and use keywords related to the information you're looking for. "
        "The query will be used for semantic similarity matching against the file contents."
    )


class SetSessionStateSchema(BaseModel):
    key: str = Field(description="The key in the session state to set")
    value: str | int | dict | list = Field(description="The value to set in session state")


class GetSessionStateSchema(BaseModel):
    key: str = Field(description="The key in the session state to retrieve")


class CalculatorSchema(BaseModel):
    expression: str = Field(
        description="The mathematical expression to evaluate. "
        "Use periods for decimals (not commas). Maximum 200 characters."
    )
