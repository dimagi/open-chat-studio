"""Comprehensive tests for tool response validation wrapper.

Note: Full integration tests with LangGraph state injection happen in higher-level tests.
These tests focus on schema modification and method wrapping logic.
"""

from typing import Annotated
from unittest.mock import Mock

import pytest
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
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


class TestWrapped:
    def test_wrapped(self, simple_tool, validator):
        wrapped = wrap_tool_with_validation(simple_tool, validator)
        node = ToolNode([wrapped])
        state = StateSchema(
            messages=["a message"],
            participant_data={},
            session_state={},
            current_context_tokens=0,
        )
        tool_call_id = "123"
        call = ToolCall(name=simple_tool.name, args={"query": "a query"}, id=tool_call_id, type="tool_call")
        call_with_context = ToolCallWithContext(
            __type="tool_call_with_context",
            tool_call=call,
            state=state,
        )
        res = node.invoke(call_with_context)
        # ToolNode returns a list of results
        assert isinstance(res, list)
        assert len(res) == 1
        command = res[0]
        assert isinstance(command, Command)
        message = command.update["messages"][0]
        assert isinstance(message, ToolMessage)
        assert message.tool_call_id == tool_call_id
        assert command.update["current_context_tokens"] == 1
