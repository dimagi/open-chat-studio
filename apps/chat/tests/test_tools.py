import json
import os
from datetime import datetime
from inspect import signature
from unittest import mock

import pytest
import pytz
from django.utils import timezone
from langchain_core.tools import StructuredTool
from time_machine import travel

from apps.chat.agent import tools
from apps.chat.agent.schemas import WeekdaysEnum
from apps.chat.agent.tools import (
    CITATION_PROMPT,
    SEARCH_TOOL_HEADER,
    TOOL_CLASS_MAP,
    DeleteReminderTool,
    SearchIndexTool,
    SearchToolConfig,
    UpdateParticipantDataTool,
    _convert_to_sync_tool,
    _get_search_tool_footer,
    _move_datetime_to_new_weekday_and_time,
    create_schedule_message,
    get_mcp_tool_instances,
)
from apps.events.models import ScheduledMessage, TimePeriod
from apps.experiments.models import AgentTools, Experiment
from apps.files.models import FileChunkEmbedding
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.events import EventActionFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.mcp_integrations import MCPServerFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.time import pretty_date


class BaseTestAgentTool:
    tool_cls: type[tools.CustomBaseTool]

    def _invoke_tool(self, session, **tool_kwargs):
        tool = self.tool_cls(experiment_session=session)
        return tool.action(**tool_kwargs)

    @staticmethod
    def schedule_params():
        return {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "Testy"}

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()


@pytest.mark.django_db()
class TestOneOffReminderTool(BaseTestAgentTool):
    tool_cls = tools.OneOffReminderTool

    def test_success(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "message": "Hi there",
            "schedule_name": "test",
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] is None
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] == 0
        assert message.custom_schedule_params["time_period"] == ""


@pytest.mark.django_db()
class TestRecurringReminderTool(BaseTestAgentTool):
    tool_cls = tools.RecurringReminderTool

    def test_with_repetitions(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": None,
            "repetitions": 2,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] == 2

    def test_with_end_time(self, session):
        datetime_due = timezone.now()
        datetime_end = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": datetime_end,
            "repetitions": None,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        assert message.end_date == datetime_end
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] is None

    def test_without_repetitions_or_end_time(self, session):
        datetime_due = timezone.now()
        tool_kwargs = {
            "datetime_due": datetime_due,
            "every": 1,
            "schedule_name": "test",
            "period": TimePeriod.DAYS,
            "message": "Hi there",
            "datetime_end": None,
            "repetitions": None,
        }

        self._invoke_tool(session, **tool_kwargs)
        message = ScheduledMessage.objects.first()
        assert message.next_trigger_date == datetime_due
        message.custom_schedule_params.pop("name")  # the name is dynamic, so lets ignore that
        assert message.custom_schedule_params["frequency"] == 1
        assert message.custom_schedule_params["time_period"] == TimePeriod.DAYS
        assert message.custom_schedule_params["prompt_text"] == "Hi there"
        assert message.custom_schedule_params["repetitions"] is None


@pytest.mark.django_db()
class TestMoveScheduledMessageDateTool(BaseTestAgentTool):
    tool_cls = tools.MoveScheduledMessageDateTool

    def test_user_cannot_set_custom_date_for_system_created_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=self.schedule_params()),
            experiment=session.experiment,
        )

        response = self._invoke_tool(
            session,
            message_id=scheduled_message.external_id,
            weekday=WeekdaysEnum.MONDAY,
            hour=8,
            minute=0,
            specified_date=timezone.now(),
        )
        assert response == "The user cannot do that. Only weekdays and time of day can be changed"

    def test_user_can_set_custom_date_for_their_messages(self, session):
        with travel("2024-01-01", tick=False):
            scheduled_message = ScheduledMessage.objects.create(
                participant=session.participant,
                team=session.team,
                experiment=session.experiment,
                custom_schedule_params=self.schedule_params(),
            )

            self._invoke_tool(
                session,
                message_id=scheduled_message.external_id,
                weekday=WeekdaysEnum.MONDAY,
                hour=8,
                minute=0,
                specified_date=timezone.now(),
            )
            scheduled_message.refresh_from_db()
            expected_date = pretty_date(scheduled_message.next_trigger_date)
            assert expected_date == "Monday, 01 January 2024 00:00:00 UTC"

    def test_update_schedule_tool(self, session):
        with travel("2024-01-01", tick=False):
            message = ScheduledMessage.objects.create(
                participant=session.participant,
                team=session.team,
                action=EventActionFactory(params=self.schedule_params()),
                experiment=session.experiment,
            )

            expected_date = pretty_date(message.next_trigger_date)
            assert expected_date == "Tuesday, 02 January 2024 00:00:00 UTC"

            response = self._invoke_tool(
                session,
                message_id=message.external_id,
                weekday=WeekdaysEnum.FRIDAY,
                hour=8,
                minute=0,
                specified_date=None,
            )
            message.refresh_from_db()
            expected_date = pretty_date(message.next_trigger_date)
            assert expected_date == "Friday, 05 January 2024 08:00:00 UTC"
            assert response == f"The schedule has been moved. The updated schedule datetime is {expected_date}"


@pytest.mark.django_db()
class TestDeleteReminderTool:
    def _invoke_tool(self, session, **tool_kwargs):
        tool = DeleteReminderTool(experiment_session=session)
        return tool.action(**tool_kwargs)

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()

    @staticmethod
    def schedule_params():
        return {"time_period": "days", "frequency": 1, "repetitions": 2, "prompt_text": "", "name": "Testy"}

    def test_user_cannot_delete_system_scheduled_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            action=EventActionFactory(params=self.schedule_params()),
            experiment=session.experiment,
        )

        response = self._invoke_tool(session, message_id=scheduled_message.external_id)
        assert response == "Cannot delete this reminder"
        scheduled_message.refresh_from_db()
        assert scheduled_message.cancelled_at is None

    def test_user_can_delete_their_scheduled_message(self, session):
        scheduled_message = ScheduledMessage.objects.create(
            participant=session.participant,
            team=session.team,
            experiment=session.experiment,
            custom_schedule_params=self.schedule_params(),
        )
        response = self._invoke_tool(session, message_id=scheduled_message.external_id)
        assert response == "The reminder has been successfully deleted."
        scheduled_message.refresh_from_db()
        assert scheduled_message.cancelled_at is not None

    def test_specified_message_does_not_exist(self, session):
        response = self._invoke_tool(session, message_id="gone with the wind")
        assert response == "Could not find this reminder"


@pytest.mark.parametrize(
    ("initial_datetime_str", "new_weekday", "new_hour", "new_minute", "expected_new_datetime_str"),
    [
        ("2024-05-08 08:00:00", 2, 12, 0, "2024-05-08 12:00:00"),  # Only time change
        ("2024-05-08 08:00:00", 0, 8, 0, "2024-05-06 08:00:00"),  # Wednesday to Monday
        ("2024-05-29 08:00:00", 4, 8, 0, "2024-05-31 08:00:00"),  # Wednesday to Friday
        ("2024-06-01 08:00:00", 0, 8, 0, "2024-05-27 08:00:00"),  # Saturday to Monday
        ("2024-06-02 08:00:00", 0, 8, 0, "2024-05-27 08:00:00"),  # Sunday to Monday
        ("2024-06-02 08:00:00", 1, 8, 0, "2024-05-28 08:00:00"),  # Sunday to Tuesday
    ],
)
def test_move_datetime_to_new_weekday_and_time(
    initial_datetime_str, new_weekday, new_hour, new_minute, expected_new_datetime_str
):
    """Test weekday and time changes. A weekday change will not cause the datetime to jump to a different week"""
    initial_datetime = datetime.strptime(initial_datetime_str, "%Y-%m-%d %H:%M:%S")
    initial_datetime = initial_datetime.astimezone(pytz.UTC)
    new_datetime = _move_datetime_to_new_weekday_and_time(
        initial_datetime, new_weekday=new_weekday, new_hour=new_hour, new_minute=new_minute
    )
    assert new_datetime.strftime("%Y-%m-%d %H:%M:%S") == expected_new_datetime_str


@pytest.mark.django_db()
def test_create_schedule_message_success():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": 1,
        "time_period": "days",
        "repetitions": 2,
    }
    start_date = timezone.now()
    end_date = timezone.now()
    response = create_schedule_message(
        experiment_session, message, name="Test", start_date=start_date, end_date=end_date, is_recurring=True, **kwargs
    )
    assert response == "Success: scheduled message created"

    scheduled_message = ScheduledMessage.objects.filter(
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        team=experiment_session.team,
    ).first()

    assert scheduled_message is not None
    assert scheduled_message.custom_schedule_params["name"] == "Test"
    assert scheduled_message.custom_schedule_params["prompt_text"] == message
    assert scheduled_message.custom_schedule_params["frequency"] == kwargs["frequency"]
    assert scheduled_message.custom_schedule_params["time_period"] == kwargs["time_period"]
    assert scheduled_message.custom_schedule_params["repetitions"] == kwargs["repetitions"]
    assert scheduled_message.action is None
    assert scheduled_message.next_trigger_date == start_date
    assert scheduled_message.end_date == end_date


@pytest.mark.django_db()
def test_create_schedule_message_invalid_form():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": "invalid_frequency",  # invalid input
        "time_period": "days",
        "repetitions": 2,
    }

    response = create_schedule_message(
        experiment_session, message, name="Test", start_date=None, is_recurring=True, **kwargs
    )
    assert response == "Could not create scheduled message"

    scheduled_message_count = ScheduledMessage.objects.filter(
        experiment=experiment_session.experiment,
        participant=experiment_session.participant,
        team=experiment_session.team,
    ).count()

    assert scheduled_message_count == 0


@pytest.mark.django_db()
def test_create_schedule_message_experiment_does_not_exist():
    experiment_session = ExperimentSessionFactory()
    message = "Test message"
    kwargs = {
        "frequency": 1,
        "time_period": "days",
        "repetitions": 2,
    }

    with mock.patch("django.db.transaction.atomic", side_effect=Experiment.DoesNotExist):
        response = create_schedule_message(
            experiment_session, message, name="Test", start_date=None, is_recurring=True, **kwargs
        )
        assert response == "Could not create scheduled message"

        scheduled_message_count = ScheduledMessage.objects.filter(
            experiment=experiment_session.experiment,
            participant=experiment_session.participant,
            team=experiment_session.team,
        ).count()

        assert scheduled_message_count == 0


@pytest.mark.django_db()
class TestUpdateParticipantDataTool:
    def _invoke_tool(self, session, **tool_kwargs):
        tool = UpdateParticipantDataTool(experiment_session=session)
        return tool.action(**tool_kwargs)

    @pytest.fixture()
    def session(self, db):
        return ExperimentSessionFactory()

    @pytest.mark.parametrize(
        "value",
        [
            "string",
            1,
            1.0,
            True,
            False,
            None,
            ["hi", "there"],
            {"key": "value"},
            [{"key": "value"}],
        ],
    )
    def test_update(self, session, value):
        response = self._invoke_tool(session, key="test", value=value)
        assert response == "The new value has been set in user data."

        assert session.participant_data_from_experiment == {"test": value}


@pytest.mark.django_db()
class TestAppendToParticipantDataTool(BaseTestAgentTool):
    tool_cls = tools.AppendToParticipantDataTool

    def test_append_when_data_does_not_exist(self, session):
        response = self._invoke_tool(session, key="test", value="new_value")
        assert response == "The value was appended to the end of the list. The new list is: ['new_value']"
        assert session.participant_data_from_experiment == {"test": ["new_value"]}

    def test_append_when_data_exists(self, session):
        # First call to create the data
        self._invoke_tool(session, key="test", value="first_value")
        # Second call to append to the existing data
        response = self._invoke_tool(session, key="test", value="second_value")
        assert (
            response
            == "The value was appended to the end of the list. The new list is: ['first_value', 'second_value']"
        )
        assert session.participant_data_from_experiment == {"test": ["first_value", "second_value"]}

    @pytest.mark.parametrize(
        ("existing_value", "new_value", "expected_result"),
        [
            ("string", "new_value", ["string", "new_value"]),
            ("string", ["new_value1", "new_value2"], ["string", "new_value1", "new_value2"]),
            ({"key": "value"}, "new_value", [{"key": "value"}, "new_value"]),
            (["val1", "val2"], "new_value", ["val1", "val2", "new_value"]),
        ],
    )
    def test_append_different_values(self, session, existing_value, new_value, expected_result):
        # First, set a non-list value using UpdateParticipantDataTool
        update_tool = UpdateParticipantDataTool(experiment_session=session)
        update_tool.action(key="test", value=existing_value)

        # Then append to it using AppendToParticipantDataTool
        response = self._invoke_tool(session, key="test", value=new_value)
        assert response == f"The value was appended to the end of the list. The new list is: {expected_result}"
        assert session.participant_data_from_experiment == {"test": expected_result}


@pytest.mark.django_db()
class TestIncrementParticipantDataTool(BaseTestAgentTool):
    tool_cls = tools.IncrementCounterTool

    def test_increment(self, session):
        response = self._invoke_tool(session, counter="test", value=1)
        assert response == "The 'test' counter has been successfully incremented. The new value is 1."

        assert session.participant_data_from_experiment == {"_counter_test": 1}


@pytest.mark.django_db()
class TestSearchIndexTool:
    def load_vector_data(self):
        current_directory = os.path.dirname(os.path.abspath(__file__))
        vector_data_file = os.path.join(current_directory, "data/vector_data.json")
        with open(vector_data_file) as json_file:
            return json.load(json_file)

    @pytest.mark.parametrize("generate_citations", [True, False])
    def test_action_returns_relevant_chunks(self, generate_citations, team, local_index_manager_mock):
        collection = CollectionFactory(team=team)
        file = FileFactory(team=team, name="the_greatness_of_fruit.txt")
        vector_data = self.load_vector_data()

        FileChunkEmbedding.objects.create(
            team=team,
            file=file,
            collection=collection,
            chunk_number=1,
            text="Oranges are nice",
            embedding=vector_data["Oranges are nice"],
            page_number=0,
        )
        FileChunkEmbedding.objects.create(
            team=team,
            file=file,
            collection=collection,
            chunk_number=2,
            text="Apples are great",
            embedding=vector_data["Apples are great"],
            page_number=0,
        )
        FileChunkEmbedding.objects.create(
            team=team,
            file=file,
            collection=collection,
            chunk_number=3,
            text="Greatness is subjective",
            embedding=vector_data["Greatness is subjective"],
            page_number=0,
        )

        # The return value of get_embedding_vector is what determines the search results.
        local_index_manager_mock.get_embedding_vector.return_value = vector_data["What are great fruit?"]
        search_config = SearchToolConfig(index_id=collection.id, max_results=2, generate_citations=generate_citations)
        result = SearchIndexTool(search_config=search_config).action(query="What are great fruit?")
        footer = _get_search_tool_footer(generate_citations)
        context_block = f"""<context>
<file>
  <file_id>{file.id}</file_id>
  <filename>the_greatness_of_fruit.txt</filename>
  <context>
    <![CDATA[Apples are great]]>
  </context>
</file>
<file>
  <file_id>{file.id}</file_id>
  <filename>the_greatness_of_fruit.txt</filename>
  <context>
    <![CDATA[Oranges are nice]]>
  </context>
</file>
</context>"""
        if generate_citations:
            expected_result = f"""
{SEARCH_TOOL_HEADER}
{CITATION_PROMPT}
{context_block}
{footer}
"""
        else:
            expected_result = f"""
{SEARCH_TOOL_HEADER}

{context_block}
{footer}
"""
        assert result == expected_result


def test_tools_present():
    for tool in AgentTools.values:
        assert tool in TOOL_CLASS_MAP


def test_convert_to_sync_tool():
    """Test that an async tool is converted to a sync tool and that the function's signature is preserved."""

    async def async_func(url: str, method: str = "GET"):
        return f"{method} {url}"

    async_tool = StructuredTool(
        name="test-tool",
        description="test-description",
        args_schema={},
        response_format="content_and_artifact",
        func=None,
        coroutine=async_func,
    )

    sync_tool = _convert_to_sync_tool(async_tool)
    assert sync_tool.coroutine is None
    assert sync_tool.func is not None
    assert str(signature(sync_tool.func)) == "(url: str, method: str = 'GET')"
    assert sync_tool.func("https://example.com", "GET") == "GET https://example.com"


@pytest.mark.django_db()
@mock.patch("apps.mcp_integrations.models.McpServer.fetch_tools")
def test_get_mcp_tool_instances(fetch_tools, team):
    async def async_func(url: str, method: str = "GET"):
        return f"{method} {url}"

    fetch_tools.return_value = [
        StructuredTool(
            name="test-tool",
            description="test-description",
            args_schema={},
            response_format="content_and_artifact",
            func=None,
            coroutine=async_func,
        )
    ]
    server = MCPServerFactory(team=team)
    node = NodeFactory(
        params={
            "mcp_tools": [f"{server.id}:test-tool"],
        }
    )
    tools = get_mcp_tool_instances(node, team)
    assert len(tools) == 1
