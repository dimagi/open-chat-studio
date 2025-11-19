import functools
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar, Union

from asgiref.sync import async_to_sync
from django.db import transaction, utils
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pgvector.django import CosineDistance

from apps.channels.models import ChannelPlatform
from apps.chat.agent import schemas
from apps.chat.agent.calculator import calculate
from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def
from apps.chat.models import ChatAttachment
from apps.events.forms import ScheduledMessageConfigForm
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import AgentTools, Experiment, ExperimentSession
from apps.files.models import FileChunkEmbedding
from apps.pipelines.models import Node
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy
from apps.teams.models import Team
from apps.teams.utils import get_slug_for_team
from apps.utils.time import pretty_date

if TYPE_CHECKING:
    from apps.assistants.models import OpenAiAssistant
    from apps.pipelines.models import Node

logger = logging.getLogger("ocs.tools")

OCS_CITATION_PATTERN = r"<CIT\s+(?P<file_id>\d+)\s*/>"

SUCCESSFUL_ATTACHMENT_MESSAGE = "* {file_id} ({name}): attached."

FILE_LINK_TEXT = "Reference link: `[{name}](file:{team_slug}:{session_id}:{file_id})`"
IMAGE_LINK_TEXT = "Reference link: `![](file:{team_slug}:{session_id}:{file_id})`"

CHUNK_TEMPLATE = """
<file>
  <file_id>{file_id}</file_id>
  <filename>{file_name}</filename>
  <context>
    <![CDATA[{chunk}]]>
  </context>
</file>
"""

CITATION_PROMPT = """**CRITICAL REQUIREMENT - MANDATORY CITATIONS:**

You MUST cite all information using this exact format: <CIT file-id />

**Citation Rules:**
- Place citations immediately after each sentence or claim that references retrieved content
- Use the specific file ID from the source document
- Example: "The revenue increased by 15% last quarter <CIT 123 />.". In this example, "123" is the file ID of the
source document.
- NEVER provide information from retrieved files without proper citations

**Response Structure:**
1. Answer the user's question thoroughly
2. Support each claim with evidence from the files
3. Ensure every factual statement includes a citation
4. If no relevant information exists in the files, explicitly state this

Failure to include proper citations will result in an incomplete response.
"""

SEARCH_TOOL_HEADER = (
    "A semantic search was executed and retrieved the following context inside <context></context> XML tags."
)
SEARCH_TOOL_BASE_FOOTER = """Use the context as your learned knowledge to better answer the user.

In your response, remember to follow these guidelines:
- If you don't know the answer, simply say that you don't know.
- If you are unsure how to answer, ask for clarification.
{citations_note}"""


def _get_search_tool_footer(with_citations: bool):
    citations_note = (
        "- Include citations for relevant files."
        if with_citations
        else "- Avoid mentioning that you obtained the information from the context."
    )
    return SEARCH_TOOL_BASE_FOOTER.format(citations_note=citations_note)


def _perform_collection_search(
    collection, query: str, max_results: int = 5, generate_citations: bool = True, include_collection_info: bool = False
) -> str:
    """
    Shared search logic for both SearchIndexTool and MultiSearchIndexTool.

    Args:
        collection: The Collection object to search
        query: The search query string
        max_results: Maximum number of results to return
        generate_citations: Whether to include citation prompt in response
        include_collection_info: Whether to include collection_id and collection_name in results

    Returns:
        Formatted search results string
    """
    query_vector = collection.get_query_vector(query)

    # Get embeddings for this collection
    embeddings = list(
        FileChunkEmbedding.objects.annotate(distance=CosineDistance("embedding", query_vector))
        .filter(collection_id=collection.id)
        .order_by("distance")
        .select_related("file")
        .only("text", "file__name")[:max_results]
    )

    if not embeddings:
        if include_collection_info:
            return f"\nThe semantic search did not return any results from collection '{collection.name}' (ID: {collection.id})."
        return "\nThe semantic search did not return any results."

    # Format results
    if include_collection_info:
        retrieved_chunks = "\n".join([
            _format_result_with_collection(embedding, collection) for embedding in embeddings
        ])
    else:
        retrieved_chunks = "\n".join([
            CHUNK_TEMPLATE.format(
                file_name=embedding.file.name, file_id=embedding.file_id, chunk=embedding.text
            ).strip()
            for embedding in embeddings
        ])

    response_template = """
{header}
{citation_prompt}
<context>
{retrieved_chunks}
</context>
{footer}
"""
    citation_prompt = CITATION_PROMPT if generate_citations else ""
    return response_template.format(
        header=SEARCH_TOOL_HEADER,
        footer=_get_search_tool_footer(generate_citations),
        retrieved_chunks=retrieved_chunks,
        citation_prompt=citation_prompt,
    )


def _format_result_with_collection(embedding: FileChunkEmbedding, collection) -> str:
    """Format a search result with collection information included."""
    return f"""
<file>
  <file_id>{embedding.file_id}</file_id>
  <filename>{embedding.file.name}</filename>
  <collection_id>{collection.id}</collection_id>
  <collection_name>{collection.name}</collection_name>
  <context>
    <![CDATA[{embedding.text}]]>
  </context>
</file>
""".strip()


@dataclass
class SearchToolConfig:
    index_id: int
    max_results: int = 5
    generate_citations: bool = True

    def get_index(self):
        from apps.documents.models import Collection

        return Collection.objects.get(id=self.index_id)


class CustomBaseTool(BaseTool):
    requires_callbacks: ClassVar[bool] = False

    experiment_session: ExperimentSession | None = None
    # Some tools like the reminder requires a chat session id in order to get back to the user later
    requires_session: bool = False
    tool_callbacks: ToolCallbacks | None = None

    def _run(self, *args, **kwargs):
        if self.requires_session and not self.experiment_session:
            return "I am unable to do this"
        try:
            return self.action(*args, **kwargs)
        except Exception:
            logger.exception("Error executing tool: %s", self.name)
            return "Something went wrong"

    async def _arun(self, *args, **kwargs) -> str:
        """Use the tool asynchronously."""
        return self._run(*args, **kwargs)

    def action(self, *args, **kwargs):
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
        specified_date: datetime | None,
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

        return f"The schedule has been moved. The updated schedule datetime is {pretty_date(message.next_trigger_date)}"


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

        scheduled_message.cancel()
        return "The reminder has been successfully deleted."


class UpdateParticipantDataTool(CustomBaseTool):
    name: str = AgentTools.UPDATE_PARTICIPANT_DATA
    description: str = "Update user data"
    requires_session: bool = True
    args_schema: type[schemas.UpdateUserDataSchema] = schemas.UpdateUserDataSchema

    def action(self, key: str, value: Any, tool_call_id: str):
        return Command(
            update={
                "participant_data": {key: value},
                "messages": [ToolMessage("The new value has been set in user data.", tool_call_id=tool_call_id)],
            }
        )


class AppendToParticipantDataTool(CustomBaseTool):
    name: str = AgentTools.APPEND_TO_PARTICIPANT_DATA
    description: str = (
        "Append a value to user data at a specific key. This will convert any existing "
        "value to a list and append the new value to the end of the list. Use this tool to "
        "track lists of items e.g. questions asked."
    )
    requires_session: bool = True
    args_schema: type[schemas.AppendToParticipantData] = schemas.AppendToParticipantData

    def action(self, key: str, value: str | int | list, tool_call_id: str, graph_state: dict):
        data_proxy = ParticipantDataProxy(graph_state, self.experiment_session)
        new_value = data_proxy.append_to_key(key, value)
        if len(new_value) > 10:
            new_value_msg = f"The last 10 items in the list are: {new_value[-10:]}"
        else:
            new_value_msg = f"The new list is: {new_value}"
        message = f"The value was appended to the end of the list. {new_value_msg}"
        return Command(
            update={
                "participant_data": {key: new_value},
                "messages": [ToolMessage(message, tool_call_id=tool_call_id)],
            }
        )


class IncrementCounterTool(CustomBaseTool):
    name: str = AgentTools.INCREMENT_COUNTER
    description: str = "Increment the value of a counter."
    requires_session: bool = True
    args_schema: type[schemas.IncrementCounterSchema] = schemas.IncrementCounterSchema

    def action(self, counter: str, value: int, tool_call_id: str, graph_state: dict):
        namespaced_key = f"_counter_{counter}"
        data_proxy = ParticipantDataProxy(graph_state, self.experiment_session)
        new_value = data_proxy.increment_key(namespaced_key, value)
        message = f"The '{counter}' counter has been successfully incremented. The new value is {new_value}."
        return Command(
            update={
                "participant_data": {namespaced_key: new_value},
                "messages": [ToolMessage(message, tool_call_id=tool_call_id)],
            }
        )


class EndSessionTool(CustomBaseTool):
    requires_callbacks: ClassVar[bool] = True
    name: str = AgentTools.END_SESSION
    description: str = (
        "End the current chat session. "
        "This will mark the session as completed. "
        "New messages will result in a new session being created."
    )

    def action(self, *args, **kwargs):
        from apps.pipelines.nodes.base import Intents

        self.tool_callbacks.register_intent(Intents.END_SESSION)
        return "Your intent to end the session has been registered."


class AttachMediaTool(CustomBaseTool):
    requires_callbacks: ClassVar[bool] = True
    name: str = AgentTools.ATTACH_MEDIA
    description: str = "Use this to attach or share media files with users."
    requires_session: bool = True
    args_schema: type[schemas.AttachMediaSchema] = schemas.AttachMediaSchema

    @cached_property
    def chat_attachment(self) -> ChatAttachment:
        chat_attachment, _ = ChatAttachment.objects.get_or_create(
            chat=self.experiment_session.chat, tool_type="ocs_attachments"
        )
        return chat_attachment

    @transaction.atomic
    def action(self, file_ids: list[int]) -> str:
        if len(file_ids) > 5:
            return "A maximum of 5 files can be attached."

        from apps.files.models import File

        response = []
        include_links = self.experiment_session.experiment_channel.platform == ChannelPlatform.WEB
        for file_id in file_ids:
            try:
                file = File.objects.get(id=file_id)
                self.chat_attachment.files.add(file_id)
                self.tool_callbacks.attach_file(file_id)
                file_response = SUCCESSFUL_ATTACHMENT_MESSAGE.format(file_id=file_id, name=file.name)

                if include_links:
                    # Only the web platform is able to render these links
                    if file.is_image:
                        link_text = IMAGE_LINK_TEXT.format(
                            file_id=file_id,
                            session_id=self.experiment_session.id,
                            team_slug=get_slug_for_team(file.team_id),
                        )
                    else:
                        link_text = FILE_LINK_TEXT.format(
                            name=file.name,
                            file_id=file_id,
                            session_id=self.experiment_session.id,
                            team_slug=get_slug_for_team(file.team_id),
                        )
                    file_response = f"{file_response} {link_text}"
                response.append(file_response)
            except File.DoesNotExist:
                response.append(f"* {file_id}: File not found.")
            except utils.IntegrityError:
                response.append(f"* {file_id}: Error fetching file.")

        resp = "File Attachment Results:\n" + "\n".join(response)
        if include_links:
            return f"{resp}\nYou may use the markdown links in your output to reference the attachments."
        return f"{resp}\nDo not use markdown links to reference the files."


class SearchIndexTool(CustomBaseTool):
    name: str = AgentTools.SEARCH_INDEX
    description: str = (
        "Performs semantic search across available documents using natural language queries. "
        "This tool analyzes the content of the documents to find relevant information, quotes, "
        "and passages that best match your query. Use this to extract specific information "
        "or find relevant sections within the available documents."
    )
    requires_session: bool = False
    args_schema: type[schemas.SearchIndexSchema] = schemas.SearchIndexSchema
    search_config: SearchToolConfig

    @transaction.atomic
    def action(self, query: str) -> str:
        """
        Do a simple search for the top most relevant file chunks based on the query provided by the user. A little query
        rewriting is automatically done by the LLM, since it decides what query to use when invoking this tool.
        """
        collection = self.search_config.get_index()
        return _perform_collection_search(
            collection=collection,
            query=query,
            max_results=self.search_config.max_results,
            generate_citations=self.search_config.generate_citations,
            include_collection_info=False,
        )


class MultiSearchIndexTool(CustomBaseTool):
    name: str = AgentTools.MULTI_SEARCH_INDEX
    description: str = (
        "Performs semantic search on a specific document collection using natural language queries. "
        "This tool analyzes the content of the specified collection to find relevant information, quotes, "
        "and passages that best match your query. Results indicate which collection they came from. "
        "To search multiple collections, make multiple tool calls with different collection_ids."
    )
    requires_session: bool = False
    args_schema: type[schemas.MultiSearchIndexSchema] = schemas.MultiSearchIndexSchema
    max_results: int = 5
    generate_citations: bool = True

    @transaction.atomic
    def action(self, collection_id: int, query: str) -> str:
        """
        Search a specific collection index for the most relevant file chunks based on the query.
        """
        from apps.documents.models import Collection

        try:
            collection = Collection.objects.get(id=collection_id, is_index=True)
        except Collection.DoesNotExist:
            return f"Collection with ID {collection_id} not found or is not a valid index."

        return _perform_collection_search(
            collection=collection,
            query=query,
            max_results=self.max_results,
            generate_citations=self.generate_citations,
            include_collection_info=True,
        )


def _move_datetime_to_new_weekday_and_time(date: datetime, new_weekday: int, new_hour: int, new_minute: int):
    current_weekday = date.weekday()
    day_diff = new_weekday - current_weekday
    return date.replace(hour=new_hour, minute=new_minute, second=0) + timedelta(days=day_diff)


class SetSessionStateTool(CustomBaseTool):
    name: str = AgentTools.SET_SESSION_STATE
    description: str = "Set a value in the session state that persists across the entire experiment session"
    requires_session: bool = True
    args_schema: type[schemas.SetSessionStateSchema] = schemas.SetSessionStateSchema

    def action(self, key: str, value: Any, tool_call_id: str):
        if key in {"user_input", "outputs", "attachments"}:
            return f"Cannot set the '{key}' key in session state - this is read-only"

        try:
            json_value = json.dumps(value)
            message = f"The value has been set in session state for key '{key}':\n{json_value}"
        except (TypeError, ValueError):
            return "Error: The value was not JSON serializable"

        return Command(
            update={
                "session_state": {key: value},
                "messages": [ToolMessage(message, tool_call_id=tool_call_id)],
            }
        )


class GetSessionStateTool(CustomBaseTool):
    name: str = AgentTools.GET_SESSION_STATE
    description: str = "Get a value from the session state that persists across the entire experiment session"
    requires_session: bool = True
    args_schema: type[schemas.GetSessionStateSchema] = schemas.GetSessionStateSchema

    def action(self, key: str, graph_state: dict):
        state = graph_state.get("session_state") or {}
        value = state.get(key)
        if value is None:
            return f"No value found for key '{key}' in session state."
        return f"The value for key '{key}' is: {value}"


class CalculatorTool(CustomBaseTool):
    name: str = AgentTools.CALCULATOR
    description: str = (
        "Evaluates mathematical expressions and returns numerical results. "
        "Supports basic arithmetic operations (+, -, *, /, //, %, **), "
        "mathematical functions (sin, cos, tan, log, sqrt, etc.), and constants (pi, e). "
        "Handles functions like min, max, sum, abs, and range. "
        "IMPORTANT: Uses period (.) for decimals - expressions with commas like '2,5 + 3,7' will be "
        "treated as separate values and return a tuple like (2, 8, 7). "
        "For decimal calculations, use '2.5 + 3.7' instead."
    )
    requires_session: bool = False
    args_schema: type[schemas.CalculatorSchema] = schemas.CalculatorSchema

    def action(self, expression: str):
        return calculate(expression)


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
            return "Could not create scheduled message"
    logger.exception(f"Could not create one-off reminder. Form errors: {form.errors}")
    return "Could not create scheduled message"


TOOL_CLASS_MAP = {
    AgentTools.MOVE_SCHEDULED_MESSAGE_DATE: MoveScheduledMessageDateTool,
    AgentTools.ONE_OFF_REMINDER: OneOffReminderTool,
    AgentTools.RECURRING_REMINDER: RecurringReminderTool,
    AgentTools.DELETE_REMINDER: DeleteReminderTool,
    AgentTools.UPDATE_PARTICIPANT_DATA: UpdateParticipantDataTool,
    AgentTools.APPEND_TO_PARTICIPANT_DATA: AppendToParticipantDataTool,
    AgentTools.INCREMENT_COUNTER: IncrementCounterTool,
    AgentTools.END_SESSION: EndSessionTool,
    AgentTools.ATTACH_MEDIA: AttachMediaTool,
    AgentTools.SEARCH_INDEX: SearchIndexTool,
    AgentTools.MULTI_SEARCH_INDEX: MultiSearchIndexTool,
    AgentTools.SET_SESSION_STATE: SetSessionStateTool,
    AgentTools.GET_SESSION_STATE: GetSessionStateTool,
    AgentTools.CALCULATOR: CalculatorTool,
}


def get_tools(experiment_session, experiment) -> list[BaseTool]:
    tool_holder = experiment.assistant if experiment.assistant else experiment
    tools = get_tool_instances(tool_holder.tools, experiment_session)
    tools.extend(get_custom_action_tools(tool_holder))
    return tools


def get_assistant_tools(assistant, experiment_session: ExperimentSession | None = None) -> list[BaseTool]:
    tools = get_tool_instances(assistant.tools, experiment_session)
    tools.extend(get_custom_action_tools(assistant))
    return tools


def get_node_tools(
    node: Node,
    experiment_session: ExperimentSession | None = None,
    tool_callbacks: ToolCallbacks | None = None,
    response_size_validator=None,
) -> list[BaseTool]:
    """Get all tools for the node and wrap them with validation if provided."""

    # Collect all tools from different sources (no changes to these functions)
    tool_names = node.params.get("tools") or []
    if node.requires_attachment_tool():
        tool_names.append(AgentTools.ATTACH_MEDIA)

    tools = get_tool_instances(tool_names, experiment_session, tool_callbacks)
    tools.extend(get_custom_action_tools(node))
    tools.extend(get_mcp_tool_instances(node, experiment_session.team))

    # Wrap all tools with validation if validator provided
    if response_size_validator:
        from apps.chat.agent.tool_response_validator import wrap_tool_with_validation

        tools = [wrap_tool_with_validation(tool, response_size_validator) for tool in tools]

    return tools


def get_mcp_tool_instances(node: Node, team: Team):
    """Fetch tools from MCP servers based on the selected tools in the node parameters."""

    mcp_tools = node.params.get("mcp_tools", [])
    if not mcp_tools:
        return []

    server_tools = defaultdict(list)
    for tool in mcp_tools:
        mcp_server_id, tool_name = tool.split(":")
        server_tools[int(mcp_server_id)].append(tool_name)

    final_tool_instances = []
    for server in team.mcpserver_set.filter(id__in=server_tools.keys()):
        remote_tools = server.fetch_tools()
        tool_instances = [_convert_to_sync_tool(tool) for tool in remote_tools if tool.name in server_tools[server.id]]
        final_tool_instances.extend(tool_instances)

    return final_tool_instances


def get_tool_instances(
    tools_list, experiment_session: ExperimentSession | None = None, tool_callbacks=None
) -> list[BaseTool]:
    tools = []
    for tool_name in tools_list:
        tool_cls = TOOL_CLASS_MAP[tool_name]
        if tool_cls.requires_callbacks and not tool_callbacks:
            raise ValueError(f"Tool {tool_name} requires callbacks but none were provided")
        tools.append(tool_cls(experiment_session=experiment_session, tool_callbacks=tool_callbacks))
    return tools


def get_custom_action_tools(action_holder: Union[Experiment, "OpenAiAssistant", "Node"]) -> list[BaseTool]:
    operations = action_holder.get_custom_action_operations().select_related("custom_action__auth_provider").all()
    return list(filter(None, [get_tool_for_custom_action_operation(operation) for operation in operations]))


def get_tool_for_custom_action_operation(custom_action_operation) -> BaseTool | None:
    custom_action = custom_action_operation.custom_action
    spec = OpenAPISpec.from_spec_dict(custom_action_operation.operation_schema)
    if not spec.paths:
        return

    auth_service = custom_action.get_auth_service()
    path = list(spec.paths)[0]
    method = spec.get_methods_for_path(path)[0]
    function_def = openapi_spec_op_to_function_def(spec, path, method)
    return function_def.build_tool(auth_service)


def _convert_to_sync_tool(tool: StructuredTool) -> StructuredTool:
    tool.func = _create_sync_wrapper(tool.coroutine)
    tool.coroutine = None
    return tool


def _create_sync_wrapper(coroutine_func):
    """Create a synchronous wrapper that preserves the original function signature."""

    @functools.wraps(coroutine_func)
    def sync_wrapper(*args, **kwargs):
        return async_to_sync(coroutine_func)(*args, **kwargs)

    return sync_wrapper
