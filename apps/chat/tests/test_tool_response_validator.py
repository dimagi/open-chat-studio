"""Comprehensive tests for tool response validation wrapper.

Note: Full integration tests with LangGraph state injection happen in higher-level tests.
These tests focus on schema modification and method wrapping logic.
"""

from typing import Annotated
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.prebuilt._internal import ToolCallWithContext
from langgraph.types import Command
from pydantic import BaseModel, Field

from apps.chat.agent.tool_response_validator import (
    ToolResponseSizeValidator,
    wrap_tool_with_validation,
)
from apps.pipelines.nodes.llm_node import StateSchema
from apps.service_providers.llm_service.token_counters import TokenCounter

# Test Fixtures and Helpers


@pytest.fixture()
def mock_token_counter():
    """Create a mock token counter."""
    counter = Mock(spec=TokenCounter)
    # Default behavior: 1 tokens per 10 characters
    counter.get_tokens_from_text.side_effect = lambda text: len(text) // 10
    return counter


@pytest.fixture()
def validator(mock_token_counter):
    """Create a validator with mock token counter."""
    return ToolResponseSizeValidator(
        token_counter=mock_token_counter,
        max_token_limit=1000,
    )


@pytest.fixture()
def simple_tool():
    """Create a simple tool without existing injected fields."""

    def dummy_action(query: str) -> str:
        return f"Result for {query}"

    return StructuredTool.from_function(
        func=dummy_action,
        name="simple_tool",
        description="A simple test tool",
    )


@pytest.fixture()
def tool_with_schema():
    """Create a tool with a defined args schema."""

    class ToolInput(BaseModel):
        query: str = Field(description="The query to process")
        limit: int = Field(default=10, description="Result limit")

    def action_with_schema(query: str, limit: int = 10) -> str:
        return f"Result for {query} (limit: {limit})"

    tool = StructuredTool.from_function(
        func=action_with_schema,
        name="tool_with_schema",
        description="Tool with schema",
        args_schema=ToolInput,
    )
    return tool


@pytest.fixture()
def tool_with_existing_state():
    """Create a tool that already has InjectedState field."""

    class ToolInputWithState(BaseModel):
        query: str = Field(description="The query")
        graph_state: Annotated[dict, InjectedState]

    def action_with_state(query: str, graph_state: dict) -> str:
        return f"Result for {query}"

    return StructuredTool.from_function(
        func=action_with_state,
        name="tool_with_state",
        description="Tool with existing state",
        args_schema=ToolInputWithState,
    )


@pytest.fixture()
def tool_with_both_injected():
    """Create a tool with both InjectedState and InjectedToolCallId fields."""

    class ToolInputComplete(BaseModel):
        query: str = Field(description="The query")
        state: Annotated[dict, InjectedState]
        call_id: Annotated[str, InjectedToolCallId]

    def action_complete(query: str, state: dict, call_id: str) -> str:
        return f"Result for {query}"

    return StructuredTool.from_function(
        func=action_complete,
        name="tool_complete",
        description="Tool with all fields",
        args_schema=ToolInputComplete,
    )


@pytest.fixture()
def state_schema():
    """Factory fixture to create StateSchema instances with custom parameters."""
    return StateSchema(
        messages=[{"role": "user", "content": "a message"}],
        participant_data={},
        session_state={},
        current_context_tokens=0,
    )


def execute_tool_call(tool, args: dict, tool_call_id: str, state: StateSchema):
    """Helper function to execute a tool call with standard setup.

    Args:
        tool: The tool to execute
        args: Arguments to pass to the tool
        tool_call_id: ID for the tool call
        state: StateSchema instance

    Returns:
        First Command from ToolNode result list
    """
    node = ToolNode([tool])
    call = ToolCall(name=tool.name, args=args, id=tool_call_id, type="tool_call")
    call_with_context = ToolCallWithContext(
        __type="tool_call_with_context",
        tool_call=call,
        state=state,
    )
    res = node.invoke(call_with_context)
    assert isinstance(res, list)
    assert len(res) > 0
    return res[0]


class TestSchemaModification:
    """Test that wrap_tool_with_validation correctly modifies tool schemas."""

    def test_adds_injected_fields_to_simple_tool(self, simple_tool, validator):
        """Test adding both InjectedState and InjectedToolCallId to a tool without them."""
        wrapped = wrap_tool_with_validation(simple_tool, validator)

        assert wrapped.args_schema is not None
        fields = wrapped.args_schema.model_fields

        # Check that graph_state field was added
        assert "graph_state" in fields
        graph_state_field = fields["graph_state"]
        # Check metadata contains InjectedState
        assert InjectedState in graph_state_field.metadata

        # Check that tool_call_id field was added
        assert "tool_call_id" in fields
        tool_call_id_field = fields["tool_call_id"]
        # Check metadata contains InjectedToolCallId
        assert InjectedToolCallId in tool_call_id_field.metadata

    def test_preserves_existing_schema_fields(self, tool_with_schema, validator):
        """Test that existing schema fields are preserved when adding injected fields."""
        original_fields = set(tool_with_schema.args_schema.model_fields.keys())
        wrapped = wrap_tool_with_validation(tool_with_schema, validator)

        new_fields = set(wrapped.args_schema.model_fields.keys())

        # Original fields should still be present
        assert original_fields.issubset(new_fields)
        # New injected fields should be added
        assert "graph_state" in new_fields
        assert "tool_call_id" in new_fields

    def test_does_not_duplicate_existing_state_field(self, tool_with_existing_state, validator):
        """Test that existing InjectedState field is detected and not duplicated."""
        original_field_count = len(tool_with_existing_state.args_schema.model_fields)
        wrapped = wrap_tool_with_validation(tool_with_existing_state, validator)
        new_field_count = len(wrapped.args_schema.model_fields)

        # Should only add tool_call_id, not graph_state
        assert new_field_count == original_field_count + 1
        assert "graph_state" in wrapped.args_schema.model_fields
        assert "tool_call_id" in wrapped.args_schema.model_fields

    def test_does_not_modify_tool_with_all_fields(self, tool_with_both_injected, validator):
        """Test that tool with both fields already present is not modified."""
        original_field_count = len(tool_with_both_injected.args_schema.model_fields)
        wrapped = wrap_tool_with_validation(tool_with_both_injected, validator)
        new_field_count = len(wrapped.args_schema.model_fields)

        assert new_field_count == original_field_count
        # Original field names should be preserved
        assert "state" in wrapped.args_schema.model_fields
        assert "call_id" in wrapped.args_schema.model_fields


class TestToolCalling:
    def test_tool_call_returns_command(self, simple_tool, validator, state_schema):
        wrapped = wrap_tool_with_validation(simple_tool, validator)
        state_schema["current_context_tokens"] = 100

        command = execute_tool_call(wrapped, {"query": "a query"}, "123", state_schema)

        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        assert message.tool_call_id == "123"
        assert command.update["current_context_tokens"] == 1  # delta, not cumulative

    def test_validation_failure_exceeds_token_limit(self, simple_tool, validator, state_schema):
        """Test that oversized responses return error messages instead of actual content."""
        wrapped = wrap_tool_with_validation(simple_tool, validator)

        # Return 10,000 characters = 1,000 tokens (exceeds 900 target limit)
        large_query = "x" * 10000
        command = execute_tool_call(wrapped, {"query": large_query}, "456", state_schema)

        # Should return an error message, not the actual response
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        assert "Error:" in message.content
        assert "tokens are available" in message.content
        assert message.tool_call_id == "456"
        # Error message token count should be in the update
        assert "current_context_tokens" in command.update

    @pytest.mark.asyncio()
    async def test_async_tool_execution(self, validator, state_schema):
        """Test that async tools (_arun) are properly validated."""

        async def async_action(query: str) -> str:
            return f"Async result for {query}"

        tool = StructuredTool.from_function(
            coroutine=async_action,
            name="async_tool",
            description="An async tool",
        )
        wrapped = wrap_tool_with_validation(tool, validator)

        node = ToolNode([wrapped])
        call = ToolCall(name=tool.name, args={"query": "test"}, id="async-123", type="tool_call")
        call_with_context = ToolCallWithContext(
            __type="tool_call_with_context",
            tool_call=call,
            state=state_schema,
        )
        res = await node.ainvoke(call_with_context)
        assert isinstance(res, list)
        assert len(res) > 0
        command = res[0]
        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        assert message.tool_call_id == "async-123"
        assert "current_context_tokens" in command.update

    def test_tool_returning_command_preserves_attributes(self, validator, state_schema):
        """Test that when a tool returns a Command, its attributes are preserved and merged."""
        tool_call_id = "cmd-123"

        def command_returning_action(query: str) -> Command:
            return Command(
                update={
                    "custom_field": "custom_value",
                    "messages": [ToolMessage(content=query, tool_call_id=tool_call_id)],
                },
                goto="special_node",
            )

        tool = StructuredTool.from_function(
            func=command_returning_action,
            name="command_tool",
            description="Tool that returns Command",
        )
        wrapped = wrap_tool_with_validation(tool, validator)

        command = execute_tool_call(wrapped, {"query": "test"}, tool_call_id, state_schema)

        # Should preserve the goto attribute
        assert command.goto == "special_node"
        # Should merge updates (custom_field + current_context_tokens)
        assert command.update["custom_field"] == "custom_value"
        assert "current_context_tokens" in command.update

    def test_validation_skipped_when_no_state(self, simple_tool, validator):
        """Test that validation is skipped when graph_state is None."""
        # This tests the case where a tool is called outside of LangGraph context
        wrapped = wrap_tool_with_validation(simple_tool, validator)

        # Call tool directly without state injection
        call = ToolCall(name=simple_tool.name, args={"query": "test"}, id="123", type="tool_call")
        result = wrapped.invoke(call)

        # Should return ToolMessage, no Command wrapping
        assert isinstance(result, ToolMessage)
        assert "Result for test" in result.content

    def test_tool_with_existing_injected_fields(self, validator, state_schema):
        """Test wrapping a tool that already has InjectedState and InjectedToolCallId."""

        class ToolInputWithBoth(BaseModel):
            query: str = Field(description="The query")
            my_state: Annotated[dict, InjectedState]
            my_call_id: Annotated[str, InjectedToolCallId]

        def action_with_both(query: str, my_state: dict, my_call_id: str) -> str:
            # Verify injected fields are accessible
            assert isinstance(my_state, dict)
            assert isinstance(my_call_id, str)
            return f"Result for {query}"

        tool = StructuredTool.from_function(
            func=action_with_both,
            name="tool_with_both",
            description="Tool with both injected fields",
            args_schema=ToolInputWithBoth,
        )
        wrapped = wrap_tool_with_validation(tool, validator)
        command = execute_tool_call(wrapped, {"query": "test"}, "both-123", state_schema)

        # Should work correctly with existing fields
        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert message.tool_call_id == "both-123"
        assert command.update["current_context_tokens"] == 1

    def test_error_message_token_count_accuracy(self, simple_tool, validator, state_schema):
        """Test that error messages themselves are token-counted correctly."""
        wrapped = wrap_tool_with_validation(simple_tool, validator)

        command = execute_tool_call(wrapped, {"query": "x" * 100000}, "error-123", state_schema)

        message = command.update["messages"][0]
        assert "Error:" in message.content

        # Verify error message token count is calculated
        error_token_count = command.update["current_context_tokens"]
        assert error_token_count > 0
        # Should be reasonable size (error messages are ~50-200 chars typically)
        assert error_token_count < 100


def test_tool_wrapping_with_langgraph(state_schema, validator):
    state_schema["current_context_tokens"] = 5
    graph = _create_graph(validator)
    res = graph.invoke(state_schema)
    assert res["messages"][-1].content == "done"
    assert res["current_context_tokens"] > 5  # expect it to be greater than the initial value


def _create_graph(validator):
    """Create a test graph that simulates parallel tool calls so that we can verify the cumulative
    token counting in the state."""
    tool1 = StructuredTool.from_function(
        func=lambda query: f"Tool1: Result for {query}",
        name="tool1",
        description="A simple test tool",
    )
    tool2 = StructuredTool.from_function(
        func=lambda query: f"Tool2: Result for {query}",
        name="tool2",
        description="A simple test tool",
    )

    workflow = StateGraph(StateSchema)

    def node1(state: StateSchema):
        last_message = state["messages"][-1]
        if not isinstance(last_message, ToolMessage):
            tool_calls = [
                ToolCall(name="tool1", args={"query": "test1"}, id="123"),
                ToolCall(name="tool2", args={"query": "test2"}, id="456"),
            ]
            return {"messages": AIMessage(content="call tool", tool_calls=tool_calls)}
        return {"messages": [AIMessage(content="done")]}

    tool_node = ToolNode(
        [
            wrap_tool_with_validation(tool1, validator),
            wrap_tool_with_validation(tool2, validator),
        ]
    )

    # Add nodes
    workflow.add_node("node1", node1)
    workflow.add_node("tools", tool_node)

    def should_continue(state: StateSchema):
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "end"

    # Define edges
    workflow.set_entry_point("node1")
    workflow.add_conditional_edges("node1", should_continue, {"tools": "tools", "end": END})
    workflow.add_edge("tools", "node1")

    return workflow.compile()
