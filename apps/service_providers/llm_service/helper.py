import json
import uuid
from json import JSONDecodeError

from langchain.agents.output_parsers.tools import ToolAgentAction, parse_ai_message_to_tool_action
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage

original_parse = parse_ai_message_to_tool_action


def custom_parse_ai_message(message) -> list[AgentAction] | AgentFinish:
    """Parse an AI message potentially containing tool_calls.
    This function is adapted from the `parse_ai_message_to_tool_action`
    function in the langchain library.
    1. Added a specific condition to skip malformed built in tool calls that
       originate from Anthropic models (where `call.get("type") == "tool_call"`,
       `call.get("name") == ""`, and `call.get("id") is None`).
    2. Ensure that tool_call_id is present in every tool_call as tool_call_id is not returned by anthropic
    built_in_tools
    3. Separate processing of custom tools and built in tools as custom tools are present in tool_calls
    whereas built_in_tools are present in additional kwargs
    """
    if not isinstance(message, AIMessage):
        raise TypeError(f"Expected AIMessage, got {type(message)}")
    tool_calls = []
    # Custom tools
    for call in getattr(message, "tool_calls", []) or []:
        # Ignore malformed built in tool case that happens for anthropic
        if call.get("type") == "tool_call" and call.get("name") == "" and call.get("id") is None:
            continue
        if isinstance(call, dict):
            name = call.get("name") or call.get("function", {}).get("name", "")
            args = call.get("args") or call.get("function", {}).get("arguments", {})
            call_id = call.get("id") or f"tool_{uuid.uuid4()}"  # Pass the generated or existing tool_call_id.

            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            tool_calls.append(
                {
                    "name": name,
                    "args": args,
                    "id": call_id,
                }
            )

    # Built in tools
    for call in message.additional_kwargs.get("tool_calls", []) or []:
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}") or "{}")
        except JSONDecodeError as e:
            raise OutputParserException(f"Could not parse tool input: {fn!r} – {e}") from e
        tool_calls.append(
            {
                "name": fn.get("name", ""),
                "args": args,
                "id": call.get("id") or f"tool_{uuid.uuid4()}",
            }
        )

    if not tool_calls:
        return AgentFinish(
            return_values={"output": message.content},
            log=str(message.content),
        )

    actions: list[AgentAction] = []
    for call in tool_calls:
        name = call["name"]
        tool_input = call["args"]
        if isinstance(tool_input, dict) and "__arg1" in tool_input:
            tool_input = tool_input["__arg1"]
        log = f"\nInvoking: `{name}` with `{tool_input}`\n"
        if message.content:
            log += f"responded: {message.content}\n"

        actions.append(
            ToolAgentAction(
                tool=name,
                tool_input=tool_input,
                log=log,
                message_log=[message],
                tool_call_id=call["id"],  # Pass the generated or existing tool_call_id.
            )
        )
    return actions
