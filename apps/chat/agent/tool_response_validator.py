import functools
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId
from langchain_core.tools.base import _is_injected_arg_type, get_all_basemodel_annotations
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, create_model

from apps.service_providers.llm_service.token_counters import TokenCounter

logger = logging.getLogger("ocs.tools")


@dataclass
class ValidationResult:
    """Result of validating a tool response size."""

    is_valid: bool
    token_count: int
    limit: int
    error_message: str | None = None
    error_message_token_count: int | None = None


class ToolResponseSizeValidator:
    """Validates tool response sizes against available context window space.

    Note: Maintains a delta token count that LangGraph merges into cumulative state.
    The 'current_context_tokens' value in state updates represents tokens added by each response.
    """

    # Keep total context under 90% of max_token_limit
    CONTEXT_SAFETY_THRESHOLD = 0.90
    # Warn if response uses more than this percentage of remaining space
    RISKY_RESPONSE_THRESHOLD = 0.5

    def __init__(self, token_counter: TokenCounter, max_token_limit: int):
        """
        Initialize validator.

        Args:
            token_counter: Token counter for the LLM provider
            max_token_limit: Maximum token limit from LlmProviderModel
        """
        self.token_counter = token_counter
        self.max_token_limit = max_token_limit
        self.target_limit = int(max_token_limit * self.CONTEXT_SAFETY_THRESHOLD)

    def validate_response(
        self,
        response: Any,
        tool_name: str,
        base_context_tokens: int = 0,
    ) -> ValidationResult:
        """
        Validate a tool response size against remaining available tokens.

        Args:
            response: Tool response (string, Command, or ToolMessage)
            tool_name: Name of the tool that generated the response
            base_context_tokens: Token count of base context (prompt + history + messages)

        Returns:
            ValidationResult with validation status and token count
        """
        # Extract content string from different response types
        content = self._extract_content(response)

        # Count tokens in the response
        token_count = self.token_counter.get_tokens_from_text(content)

        # Calculate current total and remaining tokens
        new_total = base_context_tokens + token_count
        remaining_tokens = self.target_limit - base_context_tokens

        # Check if adding this response would exceed the safety threshold
        if new_total > self.target_limit:
            error_msg = self._format_context_overflow_error(
                tool_name, token_count, remaining_tokens, base_context_tokens, self.target_limit
            )
            error_token_count = self.token_counter.get_tokens_from_text(error_msg)
            logger.warning(
                "Tool response would exceed context window: tool=%s response_tokens=%d "
                "remaining_tokens=%d current_total=%d target_limit=%d",
                tool_name,
                token_count,
                remaining_tokens,
                base_context_tokens,
                self.target_limit,
            )
            return ValidationResult(
                is_valid=False,
                token_count=token_count,
                limit=remaining_tokens,
                error_message=error_msg,
                error_message_token_count=error_token_count,
            )

        # Warn if response uses more than threshold of remaining space (risky but allowed)
        if remaining_tokens > 0 and token_count > (remaining_tokens * self.RISKY_RESPONSE_THRESHOLD):
            logger.warning(
                "Tool response uses >%.0f%% of remaining context: tool=%s response_tokens=%d "
                "remaining_tokens=%d percentage=%.1f%%",
                self.RISKY_RESPONSE_THRESHOLD * 100,
                tool_name,
                token_count,
                remaining_tokens,
                (token_count / remaining_tokens) * 100,
            )

        return ValidationResult(
            is_valid=True,
            token_count=token_count,
            limit=remaining_tokens,
        )

    def _extract_content(self, response: str | Command | ToolMessage | Any) -> str:
        """Extract string content from different response types."""
        if isinstance(response, str):
            return response
        elif isinstance(response, Command):
            # Extract content from ToolMessage in Command.update["messages"]
            messages = response.update.get("messages", [])
            if messages and isinstance(messages[0], ToolMessage):
                return str(messages[0].content)
            return ""
        elif isinstance(response, ToolMessage):
            return str(response.content)
        else:
            logger.warning(
                "Unexpected response type for token counting: %s. Converting to string.",
                type(response).__name__,
            )
            return str(response)

    def _format_context_overflow_error(
        self, tool_name: str, response_tokens: int, remaining_tokens: int, current_total: int, target_limit: int
    ) -> str:
        """Format error message for context window overflow."""
        return (
            f"Error: The {tool_name} tool returned {response_tokens:,} tokens, but only "
            f"{max(0, remaining_tokens):,} tokens are available in the context window. "
            f"Current context usage: {current_total:,} / {target_limit:,} tokens "
            f"({(current_total / target_limit * 100):.1f}%). "
            f"Please try again with more restrictive parameters to reduce the result size."
        )


def wrap_tool_with_validation(tool: BaseTool, validator: ToolResponseSizeValidator) -> BaseTool:
    """
    Wrap any BaseTool instance with response size validation.

    Works for all tool types by:
    1. Detecting if tool already has InjectedState and InjectedToolCallId fields (by annotation, not name)
    2. Adding InjectedState and InjectedToolCallId fields to args_schema if not present
    3. Wrapping _run/_arun methods to validate responses using the injected state

    This avoids code duplication and works uniformly across:
    - CustomBaseTool instances
    - StructuredTool instances (custom actions)
    - MCP tools (pre-instantiated StructuredTool)

    Args:
        tool: The tool to wrap
        validator: The validator to use for response size checking

    Returns:
        The wrapped tool with validation and modified args_schema
    """
    # Step 1: Check if tool already has InjectedState and InjectedToolCallId fields
    state_field_name = None
    tool_call_id_field_name = None
    original_schema = tool.args_schema

    if original_schema:
        # Use LangChain's utility to find fields with injected annotations
        for name, type_ in get_all_basemodel_annotations(original_schema).items():
            if _is_injected_arg_type(type_, injected_type=InjectedState):
                state_field_name = name
            elif _is_injected_arg_type(type_, injected_type=InjectedToolCallId):
                tool_call_id_field_name = name

    # Step 2: Add state and tool_call_id fields to args_schema if not already present
    added_state_field = False  # Track if we added the state field
    added_tool_call_id_field = False  # Track if we added the tool_call_id field

    if state_field_name is None:
        added_state_field = True
        state_field_name = "graph_state"  # Default name for new field

    if tool_call_id_field_name is None:
        added_tool_call_id_field = True
        tool_call_id_field_name = "tool_call_id"  # Default name for new field

    # Only modify schema if we need to add fields
    if added_state_field or added_tool_call_id_field:
        tool.args_schema = _get_patched_schema(
            original_schema,
            state_field_name if added_state_field else None,
            tool_call_id_field_name if added_tool_call_id_field else None,
        )

    # Step 3: Wrap _run and _arun with validation and field cleanup
    def _remove_injected_fields(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Remove the injected fields that we added (action method doesn't expect them)."""
        cleaned = kwargs.copy()
        if added_state_field:
            cleaned.pop(state_field_name, None)
        if added_tool_call_id_field:
            cleaned.pop(tool_call_id_field_name, None)
        return cleaned

    # Wrap _run if it exists
    if hasattr(tool, "_run"):
        original_run = tool._run

        @functools.wraps(original_run)
        def wrapped_run(*args, **kwargs):
            # Extract injected fields for validation before removing them
            graph_state = kwargs.get(state_field_name)
            tool_call_id = kwargs.get(tool_call_id_field_name)

            # Call original method
            result = original_run(*args, **_remove_injected_fields(kwargs))

            # Validate response
            return _validate_response(result, validator, tool.name, graph_state, tool_call_id)

        object.__setattr__(tool, "_run", wrapped_run)

    # Wrap _arun if it exists
    if hasattr(tool, "_arun"):
        original_arun = tool._arun

        @functools.wraps(original_arun)
        async def wrapped_arun(*args, **kwargs):
            # Extract injected fields for validation before removing them
            graph_state = kwargs.get(state_field_name)
            tool_call_id = kwargs.get(tool_call_id_field_name)

            # Call original method
            result = await original_arun(*args, **_remove_injected_fields(kwargs))

            # Validate response
            return _validate_response(result, validator, tool.name, graph_state, tool_call_id)

        object.__setattr__(tool, "_arun", wrapped_arun)

    return tool


def _get_patched_schema(
    original_schema, state_field_name: str | None, tool_call_id_field_name: str | None
) -> type[BaseModel]:
    """Create new Pydantic schema with injected fields added to original schema."""
    if original_schema:
        # Add fields to existing schema
        schema_fields = original_schema.model_fields if hasattr(original_schema, "model_fields") else {}
        new_fields = {
            **{name: (field.annotation, field) for name, field in schema_fields.items()},
        }
        if state_field_name:
            new_fields[state_field_name] = (Annotated[dict, InjectedState], None)
        if tool_call_id_field_name:
            new_fields[tool_call_id_field_name] = (Annotated[str, InjectedToolCallId], None)

        return create_model(
            f"{original_schema.__name__}WithInjected",
            **new_fields,
            __base__=BaseModel,
        )
    else:
        # If no schema exists, create a minimal one with the injected fields
        new_fields = {}
        if state_field_name:
            new_fields[state_field_name] = (Annotated[dict, InjectedState], None)
        if tool_call_id_field_name:
            new_fields[tool_call_id_field_name] = (Annotated[str, InjectedToolCallId], None)

        return create_model(
            "MinimalInjectedSchema",
            **new_fields,
            __base__=BaseModel,
        )


def _validate_response(
    result: str | Command | ToolMessage | Any,
    validator: ToolResponseSizeValidator,
    tool_name: str,
    graph_state: dict[str, Any] | None,
    tool_call_id: str | None = None,
) -> str | Command | ToolMessage:
    """Validate tool response and wrap in Command with token count.

    Args:
        result: The tool response to validate
        validator: The validator instance to use
        tool_name: Name of the tool that generated the response
        graph_state: The LangGraph state dict containing current_context_tokens
        tool_call_id: The tool call ID for creating ToolMessage

    Returns:
        Command with validated response or error message
    """
    if graph_state is not None:
        current_context_tokens = graph_state.get("current_context_tokens", 0)

        validation = validator.validate_response(
            response=result,
            tool_name=tool_name,
            base_context_tokens=current_context_tokens,
        )

        if not validation.is_valid:
            # Return error as ToolMessage in Command with updated token count
            error_msg = ToolMessage(
                content=validation.error_message,
                tool_call_id=tool_call_id or "",
            )
            return Command(
                update={
                    "messages": [error_msg],
                    "current_context_tokens": validation.error_message_token_count,
                }
            )

        logger.info(
            "Tool response validated: tool=%s tokens=%d new_cumulative=%d remaining=%d",
            tool_name,
            validation.token_count,
            current_context_tokens + validation.token_count,
            validation.limit,
        )

        # Create state update with token count
        state_update = {"current_context_tokens": validation.token_count}

        # If result is already a Command, merge the state updates and preserve all attributes
        if isinstance(result, Command):
            existing_update = result.update or {}
            merged_update = {**existing_update, **state_update}
            # Preserve all Command attributes
            return Command(
                update=merged_update,
                goto=result.goto,
                graph=getattr(result, "graph", None),
            )
        else:
            # Result is not a Command - wrap it in ToolMessage and Command
            if isinstance(result, ToolMessage):
                tool_message = result
            else:
                # result is a string or other type
                tool_message = ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call_id or "",
                )
            return Command(update={"messages": [tool_message], **state_update})

    return result
