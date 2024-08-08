import logging
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from langchain.tools.base import BaseTool

from apps.chat.agent import schemas
from apps.events.forms import ScheduledMessageConfigForm
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import AgentTools, Experiment, ExperimentSession
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
    name = AgentTools.RECURRING_REMINDER
    description = "useful to schedule recurring reminders"
    requires_session = True
    args_schema: type[schemas.RecurringReminderSchema] = schemas.RecurringReminderSchema

    def action(
        self,
        datetime_due: datetime,
        every: int,
        period: TimePeriod,
        message: str,
        datetime_end: int = None,
        repetitions: int = None,
    ):
        return create_schedule_message(
            self.experiment_session,
            message=message,
            start_date=datetime_due,
            end_date=datetime_end,
            repetitions=repetitions,
            frequency=every,
            time_period=period,
        )


class OneOffReminderTool(CustomBaseTool):
    name = AgentTools.ONE_OFF_REMINDER
    description = "useful to schedule one-off reminders"
    requires_session = True
    args_schema: type[schemas.OneOffReminderSchema] = schemas.OneOffReminderSchema

    def action(
        self,
        datetime_due: datetime,
        message: str,
        **kwargs,
    ):
        return create_schedule_message(
            self.experiment_session,
            message=message,
            start_date=datetime_due,
            repetitions=0,
            frequency=0,
            time_period=TimePeriod.HOURS,
        )


class UpdateScheduledMessageTool(CustomBaseTool):
    name = AgentTools.SCHEDULE_UPDATE
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
            # When the user specifies a new date, the bot will extract the day of the week that that day falls on
            # and pass it as a parameter to this method.
            # Since we only allow users to change the weekday of their schedules, this bahvaiour can lead to a
            # confusing conversation where the bot updated their schedule to a seemingly random date that
            # corresponds to the same weekday as the requested day. To resolve this, we simply don't allow users
            # to specify dates, but only a weekday and the time of day.
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


def create_schedule_message(
    experiment_session: ExperimentSession,
    message: str,
    start_date: datetime,
    end_date: datetime | None = None,
    **kwargs,
):
    name_id = uuid.uuid4()
    kwargs["name"] = f"schedule_message_{name_id}"
    kwargs["prompt_text"] = message
    kwargs["experiment_id"] = experiment_session.experiment.id
    repetitions_required = kwargs["repetitions"] or 0 > 0

    form = ScheduledMessageConfigForm(
        data=kwargs, experiment_id=experiment_session.experiment.id, repetitions_required=repetitions_required
    )
    if form.is_valid():
        cleaned_data = form.cleaned_data
        try:
            with transaction.atomic():
                ScheduledMessage.objects.create(
                    custom_schedule_params={
                        "name": cleaned_data["name"],
                        "prompt_text": cleaned_data["prompt_text"],
                        "frequency": cleaned_data["frequency"],
                        "time_period": cleaned_data["time_period"],
                        "repetitions": cleaned_data.get("repetitions", None),
                    },
                    experiment=experiment_session.experiment,
                    participant=experiment_session.participant,
                    team=experiment_session.team,
                    next_trigger_date=start_date,
                    end_date=end_date,
                )
            return "Success: scheduled message created"
        except Experiment.DoesNotExist:
            return "Experiment does not exist! Could not create scheduled message"
    logging.exception(f"Could not create one-off reminder. Form erros: {form.errors}")
    return "Could not create scheduled message"


TOOL_CLASS_MAP = {
    AgentTools.SCHEDULE_UPDATE: UpdateScheduledMessageTool,
    AgentTools.ONE_OFF_REMINDER: OneOffReminderTool,
    AgentTools.RECURRING_REMINDER: RecurringReminderTool,
}


def get_tools(experiment_session, for_assistant=False) -> list[BaseTool]:
    tools = []
    experiment = experiment_session.experiment
    tool_names = experiment.assistant.tools if for_assistant else experiment.tools
    for tool_name in tool_names:
        tool_cls = TOOL_CLASS_MAP[tool_name]
        tools.append(tool_cls(experiment_session=experiment_session))
    return tools


def get_assistant_tools(assistant) -> list[BaseTool]:
    tools = []
    for tool_name in assistant.tools:
        tool_cls = TOOL_CLASS_MAP[tool_name]
        tools.append(tool_cls(experiment_session=None))
    return tools
