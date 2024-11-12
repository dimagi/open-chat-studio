import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Union

from django.db import transaction
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.tools import BaseTool

from apps.chat.agent import schemas
from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.events.forms import ScheduledMessageConfigForm
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import AgentTools, Experiment, ExperimentSession, ParticipantData
from apps.utils.time import pretty_date

if TYPE_CHECKING:
    from apps.assistants.models import OpenAiAssistant


class CustomBaseTool(BaseTool):
    experiment_session: ExperimentSession | None = None
    # Some tools like the reminder requires a chat session id in order to get back to the user later
    requires_session: bool = False

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
    name: str = AgentTools.RECURRING_REMINDER
    description: str = "Schedule recurring reminders"
    requires_session: bool = True
    args_schema: type[schemas.RecurringReminderSchema] = schemas.RecurringReminderSchema

    def action(
        self,
        schedule_name: str,
        datetime_due: datetime,
        every: int,
        period: TimePeriod,
        message: str,
        datetime_end: datetime | None = None,
        repetitions: int | None = None,
    ):
        return create_schedule_message(
            self.experiment_session,
            message=message,
            name=schedule_name,
            start_date=datetime_due,
            end_date=datetime_end,
            repetitions=repetitions,
            frequency=every,
            time_period=period,
            is_recurring=True,
        )


class OneOffReminderTool(CustomBaseTool):
    name: str = AgentTools.ONE_OFF_REMINDER
    description: str = "Schedule one-off reminders"
    requires_session: bool = True
    args_schema: type[schemas.OneOffReminderSchema] = schemas.OneOffReminderSchema

    def action(
        self,
        datetime_due: datetime,
        message: str,
        schedule_name: str,
    ):
        return create_schedule_message(
            self.experiment_session, message=message, name=schedule_name, start_date=datetime_due, is_recurring=False
        )


class MoveScheduledMessageDateTool(CustomBaseTool):
    name: str = AgentTools.MOVE_SCHEDULED_MESSAGE_DATE
    description: str = "Move the day and time that the scheduled message should trigger"
    requires_session: bool = True
    args_schema: type[schemas.ScheduledMessageSchema] = schemas.ScheduledMessageSchema

    def action(
        self,
        message_id: str,
        weekday: schemas.WeekdaysEnum,
        hour: int,
        minute: int,
        specified_date: datetime | None = None,
    ):
        try:
            message = ScheduledMessage.objects.get(
                participant=self.experiment_session.participant, external_id=message_id
            )
        except ScheduledMessage.DoesNotExist:
            return f"The scheduled message with id={message_id} was not found."
        if specified_date and message.was_created_by_system:
            # When the user specifies a new date, the bot will extract the day of the week that that day falls on
            # and pass it as a parameter to this method.
            # Since we only allow users to change the weekday of their schedules, this bahvaiour can lead to a
            # confusing conversation where the bot updated their schedule to a seemingly random date that
            # corresponds to the same weekday as the requested day. To resolve this, we simply don't allow users
            # to specify dates, but only a weekday and the time of day.
            return "The user cannot do that. Only weekdays and time of day can be changed"

        # the datetime object regard Monday as day 0 whereas the llm regards it as day 1
        if specified_date:
            message.next_trigger_date = specified_date
        else:
            weekday_int = weekday.value - 1
            message.next_trigger_date = _move_datetime_to_new_weekday_and_time(
                message.next_trigger_date, weekday_int, hour, minute
            )
        message.save()

        return f"The new datetime is {pretty_date(message.next_trigger_date)}"


class DeleteReminderTool(CustomBaseTool):
    name: str = AgentTools.DELETE_REMINDER
    description: str = "Delete scheduled reminders"
    requires_session: bool = True
    args_schema: type[schemas.DeleteReminderSchema] = schemas.DeleteReminderSchema

    def action(self, message_id: str):
        try:
            scheduled_message = self.experiment_session.participant.schduled_messages.get(external_id=message_id)
            if scheduled_message.was_created_by_system:
                # Participants should not be able to delete a scheduled message that was created through an action
                return "Cannot delete this reminder"
        except ScheduledMessage.DoesNotExist:
            return "Could not find this reminder"

        scheduled_message.delete()
        return "Success"


class UpdateParticipantDataTool(CustomBaseTool):
    name: str = AgentTools.UPDATE_PARTICIPANT_DATA
    description: str = "Update user data"
    requires_session: bool = True
    args_schema: type[schemas.UpdateUserDataSchema] = schemas.UpdateUserDataSchema

    @transaction.atomic
    def action(self, key: str, value: Any):
        try:
            participant_data = ParticipantData.objects.for_experiment(self.experiment_session.experiment).get(
                participant=self.experiment_session.participant
            )
            participant_data.data[key] = value
            participant_data.save()
        except ParticipantData.DoesNotExist:
            ParticipantData.objects.create(
                participant=self.experiment_session.participant,
                content_object=self.experiment_session.experiment,
                team=self.experiment_session.team,
                data={key: value},
            )
        return "Success"


def _move_datetime_to_new_weekday_and_time(date: datetime, new_weekday: int, new_hour: int, new_minute: int):
    current_weekday = date.weekday()
    day_diff = new_weekday - current_weekday
    return date.replace(hour=new_hour, minute=new_minute, second=0) + timedelta(days=day_diff)


def create_schedule_message(
    experiment_session: ExperimentSession,
    message: str,
    name: str,
    start_date: datetime,
    is_recurring: bool,
    end_date: datetime | None = None,
    **kwargs,
):
    kwargs["name"] = name
    kwargs["prompt_text"] = message
    kwargs["experiment_id"] = experiment_session.experiment.id

    if is_recurring:
        non_required_fields = ["repetitions"]
    else:
        kwargs["repetitions"] = 0
        non_required_fields = ["frequency", "time_period"]

    form = ScheduledMessageConfigForm(
        data=kwargs, experiment_id=experiment_session.experiment.id, non_required_fields=non_required_fields
    )
    if form.is_valid():
        cleaned_data = form.cleaned_data
        try:
            with transaction.atomic():
                ScheduledMessage.objects.create(
                    custom_schedule_params={
                        "name": cleaned_data["name"],
                        "prompt_text": cleaned_data["prompt_text"],
                        "frequency": cleaned_data.get("frequency"),
                        "time_period": cleaned_data.get("time_period"),
                        "repetitions": cleaned_data.get("repetitions"),
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
    logging.exception(f"Could not create one-off reminder. Form errors: {form.errors}")
    return "Could not create scheduled message"


TOOL_CLASS_MAP = {
    AgentTools.MOVE_SCHEDULED_MESSAGE_DATE: MoveScheduledMessageDateTool,
    AgentTools.ONE_OFF_REMINDER: OneOffReminderTool,
    AgentTools.RECURRING_REMINDER: RecurringReminderTool,
    AgentTools.DELETE_REMINDER: DeleteReminderTool,
    AgentTools.UPDATE_PARTICIPANT_DATA: UpdateParticipantDataTool,
}


def get_tools(experiment_session, experiment) -> list[BaseTool]:
    tools = []
    tool_holder = experiment.assistant if experiment.assistant else experiment
    for tool_name in tool_holder.tools:
        tool_cls = TOOL_CLASS_MAP[tool_name]
        tools.append(tool_cls(experiment_session=experiment_session))

    tools.extend(get_custom_action_tools(tool_holder))

    return tools


def get_assistant_tools(assistant) -> list[BaseTool]:
    tools = []
    for tool_name in assistant.tools:
        tool_cls = TOOL_CLASS_MAP[tool_name]
        tools.append(tool_cls(experiment_session=None))

    tools.extend(get_custom_action_tools(assistant))

    return tools


def get_custom_action_tools(action_holder: Union["Experiment", "OpenAiAssistant"]) -> list[BaseTool]:
    operations = action_holder.custom_action_operations.select_related(
        "custom_action", "custom_action__auth_provider"
    ).all()
    return list(filter(None, [get_tool_for_custom_action_operation(operation) for operation in operations]))


def get_tool_for_custom_action_operation(custom_action_operation) -> BaseTool | None:
    custom_action = custom_action_operation.custom_action
    spec = OpenAPISpec.from_spec_dict(custom_action.api_schema)
    if not spec.paths:
        return

    auth_service = custom_action.get_auth_service()
    ops_by_id = custom_action.get_operations_by_id()
    operation = ops_by_id.get(custom_action_operation.operation_id)
    if not operation:
        return

    function_def = openapi_spec_op_to_function_def(spec, operation.path, operation.method)
    return function_def.build_tool(auth_service)
